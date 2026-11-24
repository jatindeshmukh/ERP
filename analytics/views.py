from datetime import datetime, time
from io import StringIO
import json

import pandas as pd
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from tickets.models import Ticket


PENDING_STATUSES = (Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS)
RESOLVED_STATUSES = (Ticket.Status.RESOLVED, Ticket.Status.CLOSED)


def is_admin(user):
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    return profile is not None and profile.role == 'Admin'


def chart_payload(labels, values):
    return json.dumps({'labels': list(labels), 'values': list(values)})


def filtered_tickets(request):
    start_value = request.GET.get('start_date', '').strip()
    end_value = request.GET.get('end_date', '').strip()
    tickets = Ticket.objects.select_related('assigned_support')

    start_date = None
    end_date = None
    try:
        if start_value:
            start_date = datetime.strptime(start_value, '%Y-%m-%d').date()
        if end_value:
            end_date = datetime.strptime(end_value, '%Y-%m-%d').date()
    except ValueError:
        start_date = None
        end_date = None
        start_value = ''
        end_value = ''

    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date
        start_value, end_value = end_value, start_value
    current_timezone = timezone.get_current_timezone()
    if start_date:
        start_datetime = timezone.make_aware(datetime.combine(start_date, time.min), current_timezone)
        tickets = tickets.filter(created_date__gte=start_datetime)
    if end_date:
        end_datetime = timezone.make_aware(datetime.combine(end_date, time.max), current_timezone)
        tickets = tickets.filter(created_date__lte=end_datetime)
    return tickets, start_value, end_value


def ticket_records(tickets):
    return list(tickets.values(
        'ticket_id',
        'client_name',
        'issue_title',
        'category',
        'priority',
        'status',
        'assigned_support_id',
        'assigned_support__username',
        'assigned_support__first_name',
        'assigned_support__last_name',
        'created_date',
        'resolution_date',
    ))


def normalize_issue(value):
    return ' '.join(str(value or '').split()).casefold()


def protect_csv_formula(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def issue_patterns(frame):
    if frame.empty:
        return []
    pattern_columns = ['client_name', 'issue_title']
    if 'created_date' in frame.columns:
        pattern_columns.append('created_date')
    issues = frame[pattern_columns].copy()
    if 'created_date' in issues.columns:
        issues = issues.sort_values('created_date', kind='stable')
    issues['client_name'] = issues['client_name'].fillna('').astype(str).str.strip()
    issues['normalized_client'] = issues['client_name'].str.casefold()
    issues['issue_title'] = (
        issues['issue_title'].fillna('').astype(str).str.strip()
        .str.replace(r'\s+', ' ', regex=True)
    )
    issues['normalized_issue'] = issues['issue_title'].map(normalize_issue)
    issues = issues[
        (issues['client_name'] != '')
        & (issues['normalized_issue'] != '')
    ]
    grouped = (
        issues.groupby(['normalized_client', 'normalized_issue'], as_index=False)
        .agg(
            client_name=('client_name', 'first'),
            issue_title=('issue_title', 'first'),
            occurrences=('issue_title', 'size'),
        )
    )
    grouped = grouped[grouped['occurrences'] > 1].sort_values(
        ['occurrences', 'client_name', 'issue_title'],
        ascending=[False, True, True],
    )
    return [
        {
            'client_name': str(row.client_name),
            'issue_title': str(row.issue_title),
            'count': int(row.occurrences),
        }
        for row in grouped.itertuples()
    ]


@login_required
def analytics_dashboard(request):
    if not is_admin(request.user):
        raise PermissionDenied('Complete analytics is restricted to administrators.')

    tickets, start_value, end_value = filtered_tickets(request)
    records = ticket_records(tickets)

    if records:
        frame = pd.DataFrame.from_records(records)
        frame['created_date'] = pd.to_datetime(frame['created_date'], utc=True)
        frame['resolution_date'] = pd.to_datetime(frame['resolution_date'], errors='coerce', utc=True)
        if timezone.is_aware(timezone.now()):
            frame['created_local'] = frame['created_date'].dt.tz_convert(timezone.get_current_timezone())
        else:
            frame['created_local'] = frame['created_date'].dt.tz_localize('UTC')
    else:
        frame = pd.DataFrame(columns=[
            'ticket_id', 'client_name', 'issue_title', 'category', 'priority', 'status',
            'assigned_support_id', 'assigned_support__username',
            'assigned_support__first_name', 'assigned_support__last_name',
            'created_date', 'resolution_date',
        ])

    total_tickets = len(frame)
    open_tickets = int(frame['status'].eq(Ticket.Status.OPEN).sum()) if total_tickets else 0
    resolved_tickets = int(frame['status'].isin(RESOLVED_STATUSES).sum()) if total_tickets else 0
    critical_issues = int(frame['priority'].eq(Ticket.Priority.CRITICAL).sum()) if total_tickets else 0

    resolution_days = []
    if total_tickets:
        resolved_frame = frame[
            frame['status'].isin(RESOLVED_STATUSES)
            & frame['resolution_date'].notna()
            & frame['created_date'].notna()
        ].copy()
        if not resolved_frame.empty:
            resolved_frame['created_day'] = resolved_frame['created_local'].dt.strftime('%Y-%m-%d')
            resolved_frame['resolved_day'] = resolved_frame['resolution_date'].dt.strftime('%Y-%m-%d')
            resolved_frame['resolution_days'] = (
                pd.to_datetime(resolved_frame['resolved_day'])
                - pd.to_datetime(resolved_frame['created_day'])
            ).dt.total_seconds().div(86400)
            resolved_frame = resolved_frame[resolved_frame['resolution_days'] >= 0]
            resolution_days = resolved_frame['resolution_days'].tolist()

    average_resolution_days = sum(resolution_days) / len(resolution_days) if resolution_days else 0

    def executive_name(row):
        full_name = ' '.join(filter(None, [
            str(row.get('assigned_support__first_name') or ''),
            str(row.get('assigned_support__last_name') or ''),
        ])).strip()
        return full_name or row.get('assigned_support__username') or 'Unassigned'

    executive_performance = []
    pending_workload = []
    if total_tickets:
        assigned_frame = frame[frame['assigned_support_id'].notna()].copy()
        if not assigned_frame.empty:
            assigned_frame['executive'] = assigned_frame.apply(executive_name, axis=1)
            performance = assigned_frame.groupby('executive', dropna=False).agg(
                assigned=('ticket_id', 'count'),
                resolved=('status', lambda values: values.isin(RESOLVED_STATUSES).sum()),
                pending=('status', lambda values: values.isin(PENDING_STATUSES).sum()),
            ).reset_index()
            performance['resolution_rate'] = (
                performance['resolved'] / performance['assigned'] * 100
            ).round(1)
            executive_performance = [
                {
                    'name': row.executive,
                    'assigned': int(row.assigned),
                    'resolved': int(row.resolved),
                    'pending': int(row.pending),
                    'resolution_rate': float(row.resolution_rate),
                }
                for row in performance.sort_values(['assigned', 'executive'], ascending=[False, True]).itertuples()
            ]
            pending_workload = [
                {
                    'name': row.executive,
                    'pending': int(row.pending),
                }
                for row in performance[performance['pending'] > 0].sort_values(
                    ['pending', 'executive'],
                    ascending=[False, True],
                ).itertuples()
            ]

    category_distribution = []
    priority_distribution = []
    if total_tickets:
        category_counts = frame['category'].fillna('Other').value_counts()
        category_distribution = [
            {'name': str(name), 'count': int(count)}
            for name, count in category_counts.items()
        ]
        priority_counts = frame['priority'].fillna('Unknown').value_counts()
        priority_distribution = [
            {'name': str(name), 'count': int(count)}
            for name, count in priority_counts.items()
        ]

    frequent_issues = []
    if total_tickets:
        issue_counts = (
            frame['issue_title'].fillna('Unknown')
            .str.strip()
            .replace('', 'Unknown')
            .value_counts()
            .head(10)
        )
        frequent_issues = [
            {'title': str(title), 'count': int(count)}
            for title, count in issue_counts.items()
        ]

    daily_trends = []
    monthly_trends = []
    if total_tickets:
        frame['day'] = frame['created_local'].dt.strftime('%Y-%m-%d')
        daily_counts = frame.groupby('day').size().sort_index()
        daily_trends = [
            {'label': str(day), 'count': int(count)}
            for day, count in daily_counts.items()
        ]
        frame['month'] = frame['created_local'].dt.strftime('%Y-%m')
        monthly_counts = frame.groupby('month').size().sort_index()
        monthly_trends = [
            {'label': str(month), 'count': int(count)}
            for month, count in monthly_counts.items()
        ]

    highest_complaint_category = (
        category_distribution[0] if category_distribution else {'name': 'N/A', 'count': 0}
    )

    longest_resolution_priority = {'name': 'N/A', 'average_days': 0}
    if total_tickets:
        resolved_frame = frame[
            frame['status'].isin(RESOLVED_STATUSES)
            & frame['resolution_date'].notna()
            & frame['created_date'].notna()
        ].copy()
        if not resolved_frame.empty:
            resolved_frame['resolution_hours'] = (
                pd.to_datetime(resolved_frame['resolution_date'].dt.strftime('%Y-%m-%d'))
                - pd.to_datetime(resolved_frame['created_local'].dt.strftime('%Y-%m-%d'))
            ).dt.total_seconds().div(3600)
            resolved_frame = resolved_frame[resolved_frame['resolution_hours'] >= 0]
            if not resolved_frame.empty:
                priority_resolution = (
                    resolved_frame.groupby('priority')['resolution_hours'].mean().sort_values(ascending=False)
                )
                longest_resolution_priority = {
                    'name': str(priority_resolution.index[0]),
                    'average_days': round(float(priority_resolution.iloc[0]) / 24, 2),
                }

    executive_highest_pending = (
        pending_workload[0] if pending_workload else {'name': 'N/A', 'pending': 0}
    )
    busiest_month = (
        max(monthly_trends, key=lambda item: item['count'])
        if monthly_trends
        else {'label': 'N/A', 'count': 0}
    )
    repeated_client_issues = issue_patterns(frame)

    context = {
        'start_date': start_value,
        'end_date': end_value,
        'is_empty': total_tickets == 0,
        'total_tickets': total_tickets,
        'open_tickets': open_tickets,
        'resolved_tickets': resolved_tickets,
        'critical_issues': critical_issues,
        'average_resolution_days': round(average_resolution_days, 2),
        'executive_performance': executive_performance,
        'pending_workload': pending_workload,
        'category_distribution': category_distribution,
        'priority_distribution': priority_distribution,
        'daily_trends': daily_trends,
        'monthly_trends': monthly_trends,
        'frequent_issues': frequent_issues,
        'repeated_client_issues': repeated_client_issues,
        'highest_complaint_category': highest_complaint_category,
        'longest_resolution_priority': longest_resolution_priority,
        'executive_highest_pending': executive_highest_pending,
        'busiest_month': busiest_month,
        'daily_chart': chart_payload(
            [item['label'] for item in daily_trends],
            [item['count'] for item in daily_trends],
        ),
        'monthly_chart': chart_payload(
            [item['label'] for item in monthly_trends],
            [item['count'] for item in monthly_trends],
        ),
        'category_chart': chart_payload(
            [item['name'] for item in category_distribution],
            [item['count'] for item in category_distribution],
        ),
        'priority_chart': chart_payload(
            [item['name'] for item in priority_distribution],
            [item['count'] for item in priority_distribution],
        ),
    }
    return render(request, 'analytics/dashboard.html', context)


@login_required
def analytics_export(request):
    if not is_admin(request.user):
        raise PermissionDenied('Complete analytics is restricted to administrators.')

    tickets, _, _ = filtered_tickets(request)
    records = ticket_records(tickets)
    columns = [
        'Report Type',
        'Ticket ID',
        'Client Name',
        'Issue Title',
        'Category',
        'Priority',
        'Status',
        'Assigned Executive',
        'Created Date',
        'Resolution Date',
        'Resolution Days',
        'Occurrence Count',
        'Insight',
    ]

    if records:
        frame = pd.DataFrame.from_records(records)
        frame['created_date'] = pd.to_datetime(frame['created_date'], utc=True)
        frame['created_local'] = frame['created_date'].dt.tz_convert(timezone.get_current_timezone())
        frame['resolution_date'] = pd.to_datetime(frame['resolution_date'], errors='coerce')
        frame['assigned_executive'] = (
            frame['assigned_support__first_name'].fillna('').astype(str).str.strip()
            + ' '
            + frame['assigned_support__last_name'].fillna('').astype(str).str.strip()
        ).str.strip()
        missing_name = frame['assigned_executive'].eq('')
        frame.loc[missing_name, 'assigned_executive'] = (
            frame.loc[missing_name, 'assigned_support__username'].fillna('Unassigned')
        )
        frame['created_day'] = frame['created_local'].dt.strftime('%Y-%m-%d')
        frame['resolution_day'] = frame['resolution_date'].dt.strftime('%Y-%m-%d')
        frame['resolution_days'] = (
            pd.to_datetime(frame['resolution_day'])
            - pd.to_datetime(frame['created_day'])
        ).dt.total_seconds().div(86400)
        frame.loc[
            ~frame['status'].isin(RESOLVED_STATUSES)
            | frame['resolution_day'].isna()
            | (frame['resolution_days'] < 0),
            'resolution_days',
        ] = pd.NA

        patterns = issue_patterns(frame)
        pattern_counts = {
            (
                item['client_name'].casefold(),
                normalize_issue(item['issue_title']),
            ): item['count']
            for item in patterns
        }
        normalized_issues = frame['issue_title'].map(normalize_issue)
        normalized_clients = frame['client_name'].fillna('').astype(str).str.strip().str.casefold()
        ticket_rows = pd.DataFrame({
            'Report Type': 'Ticket',
            'Ticket ID': frame['ticket_id'],
            'Client Name': frame['client_name'],
            'Issue Title': frame['issue_title'],
            'Category': frame['category'],
            'Priority': frame['priority'],
            'Status': frame['status'],
            'Assigned Executive': frame['assigned_executive'],
            'Created Date': frame['created_local'].dt.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'Resolution Date': frame['resolution_day'],
            'Resolution Days': frame['resolution_days'],
            'Occurrence Count': [
                pattern_counts.get((client, issue), 1)
                for client, issue in zip(normalized_clients, normalized_issues)
            ],
            'Insight': '',
        })

        insight_rows = []
        categories = frame['category'].fillna('Unknown').value_counts()
        insight_rows.extend({
            'Report Type': 'Category',
            'Category': str(category),
            'Occurrence Count': int(count),
            'Insight': 'Ticket volume by complaint category',
        } for category, count in categories.items())

        resolved = frame[
            frame['status'].isin(RESOLVED_STATUSES)
            & frame['resolution_days'].notna()
        ]
        if not resolved.empty:
            priority_averages = resolved.groupby('priority')['resolution_days'].mean().sort_values(ascending=False)
            insight_rows.extend({
                'Report Type': 'Priority resolution',
                'Priority': str(priority),
                'Resolution Days': round(float(days), 2),
                'Insight': 'Average resolution time by priority in days',
            } for priority, days in priority_averages.items())

        pending = frame[frame['status'].isin(PENDING_STATUSES) & frame['assigned_support_id'].notna()]
        if not pending.empty:
            pending_counts = pending.groupby('assigned_executive').size().sort_values(ascending=False)
            insight_rows.extend({
                'Report Type': 'Executive workload',
                'Assigned Executive': str(executive),
                'Occurrence Count': int(count),
                'Insight': 'Pending tickets assigned to executive',
            } for executive, count in pending_counts.items())

        monthly = frame.groupby(frame['created_local'].dt.strftime('%Y-%m')).size().sort_values(ascending=False)
        insight_rows.extend({
            'Report Type': 'Monthly volume',
            'Created Date': str(month),
            'Occurrence Count': int(count),
            'Insight': 'Ticket volume by month',
        } for month, count in monthly.items())

        insight_rows.extend({
            'Report Type': 'Repeated client issue',
            'Client Name': item['client_name'],
            'Issue Title': item['issue_title'],
            'Occurrence Count': item['count'],
            'Insight': 'Same issue reported repeatedly by this client',
        } for item in patterns)
        report = pd.concat(
            [ticket_rows, pd.DataFrame(insight_rows, columns=columns)],
            ignore_index=True,
        ).reindex(columns=columns)
    else:
        report = pd.DataFrame(columns=columns)

    output = StringIO()
    report = report.map(protect_csv_formula)
    report.to_csv(output, index=False)
    response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="ticket-bi-report-{timezone.localdate().isoformat()}.csv"'
    )
    return response
