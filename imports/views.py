from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.utils import DataError, IntegrityError
from django.shortcuts import render
from django.contrib.auth import get_user_model
from django.utils import timezone

import pandas as pd

from tickets.models import Ticket, TicketStatusHistory
from .forms import TicketCSVUploadForm


MAX_UPLOAD_SIZE = 5 * 1024 * 1024
REQUIRED_COLUMNS = [
    'Ticket ID',
    'Client Name',
    'Issue Title',
    'Description',
    'Category',
    'Priority',
    'Status',
]


def is_admin(user):
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    return profile is not None and profile.role == 'Admin'


def clean_text(value):
    if pd.isna(value):
        return ''
    return str(value).strip()


def is_dangerous_formula(value):
    return value.startswith(('=', '+', '-', '@')) and len(value) > 0


@login_required
def import_dashboard(request):
    if not is_admin(request.user):
        raise PermissionDenied('CSV ticket imports are restricted to administrators.')

    summary = None
    form = TicketCSVUploadForm()

    if request.method == 'POST':
        form = TicketCSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data['file']
            try:
                upload.seek(0)
                frame = pd.read_csv(
                    upload,
                    dtype=str,
                    keep_default_na=False,
                    encoding='utf-8-sig',
                    nrows=5001,
                    na_filter=False,
                )
            except Exception as exc:
                form.add_error('file', f'The CSV could not be parsed safely: {exc}')
            else:
                if len(frame) > 5000:
                    form.add_error('file', 'The CSV contains more than 5000 rows.')
                else:
                    frame.columns = [str(column).strip() for column in frame.columns]
                    if len(set(frame.columns)) != len(frame.columns):
                        form.add_error(
                            'file',
                            'The CSV contains duplicate column names after trimming whitespace.',
                        )
                        return render(request, 'imports/dashboard.html', {
                            'form': form,
                            'summary': summary,
                            'required_columns': REQUIRED_COLUMNS,
                            'max_upload_mb': MAX_UPLOAD_SIZE // (1024 * 1024),
                        })
                    missing_columns = [
                        column for column in REQUIRED_COLUMNS
                        if column not in frame.columns
                    ]
                    if missing_columns:
                        form.add_error(
                            'file',
                            'Missing required columns: ' + ', '.join(missing_columns),
                        )
                    else:
                        summary = process_csv(request.user, frame)

    return render(request, 'imports/dashboard.html', {
        'form': form,
        'summary': summary,
        'required_columns': REQUIRED_COLUMNS,
        'max_upload_mb': MAX_UPLOAD_SIZE // (1024 * 1024),
    })


def process_csv(importing_user, frame):
    total_rows = len(frame)
    row_results = []
    valid_tickets = []
    seen_ticket_ids = set()

    existing_ids = set(
        Ticket.objects.filter(
            ticket_id__in=[
                clean_text(value)
                for value in frame['Ticket ID'].tolist()
            ]
        ).values_list('ticket_id', flat=True)
    )
    username_map = {
        user.username: user
        for user in get_user_model().objects.filter(
            username__in=[
                clean_text(value)
                for value in frame.get('Assigned Support', pd.Series(dtype=str)).tolist()
                if clean_text(value)
            ],
        ).filter(
            Q(is_superuser=True) | Q(profile__role='Support Executive')
        )
    }

    category_values = {value for value, _ in Ticket.Category.choices}
    priority_values = {value for value, _ in Ticket.Priority.choices}
    status_values = {value for value, _ in Ticket.Status.choices}
    duplicate_rows = 0

    for row_number, row in frame.iterrows():
        csv_row_number = int(row_number) + 2
        errors = []
        ticket_id = clean_text(row.get('Ticket ID'))
        client_name = clean_text(row.get('Client Name'))
        issue_title = clean_text(row.get('Issue Title'))
        description = clean_text(row.get('Description'))
        category = clean_text(row.get('Category'))
        priority = clean_text(row.get('Priority'))
        status = clean_text(row.get('Status'))
        resolution_date_text = clean_text(row.get('Resolution Date', ''))
        assigned_support_text = clean_text(row.get('Assigned Support', ''))

        if not ticket_id:
            errors.append('Ticket ID is missing.')
        elif len(ticket_id) > 30:
            errors.append('Ticket ID exceeds 30 characters.')
        elif ticket_id in existing_ids:
            duplicate_rows += 1
            errors.append('Duplicate Ticket ID already exists in the database.')
        elif ticket_id in seen_ticket_ids:
            duplicate_rows += 1
            errors.append('Duplicate Ticket ID appears multiple times in this CSV.')

        if not client_name:
            errors.append('Client Name is missing.')
        elif len(client_name) > 200:
            errors.append('Client Name exceeds 200 characters.')
        if not issue_title:
            errors.append('Issue Title is missing.')
        elif len(issue_title) > 255:
            errors.append('Issue Title exceeds 255 characters.')
        if not description:
            errors.append('Description is missing.')
        if category not in category_values:
            errors.append(
                f'Invalid Category. Allowed values: {", ".join(sorted(category_values))}.'
            )
        if priority not in priority_values:
            errors.append(
                f'Invalid Priority. Allowed values: {", ".join(sorted(priority_values))}.'
            )
        if status not in status_values:
            errors.append(
                f'Invalid Status. Allowed values: {", ".join(sorted(status_values))}.'
            )

        resolution_date = None
        if resolution_date_text:
            try:
                resolution_date = pd.to_datetime(
                    resolution_date_text,
                    format='%Y-%m-%d',
                    errors='raise',
                ).date()
            except (TypeError, ValueError):
                errors.append('Resolution Date must use YYYY-MM-DD format.')
        if status == Ticket.Status.RESOLVED and resolution_date is None:
            errors.append('Resolution Date is required when Status is Resolved.')

        assigned_support = None
        if assigned_support_text:
            assigned_support = username_map.get(assigned_support_text)
            if assigned_support is None:
                errors.append('Assigned Support username does not exist.')

        for field_name, value in (
            ('Ticket ID', ticket_id),
            ('Client Name', client_name),
            ('Issue Title', issue_title),
            ('Description', description),
            ('Category', category),
            ('Priority', priority),
            ('Status', status),
            ('Assigned Support', assigned_support_text),
        ):
            if is_dangerous_formula(value):
                errors.append(f'{field_name} contains a prohibited formula expression.')

        if ticket_id:
            seen_ticket_ids.add(ticket_id)

        if errors:
            row_results.append({
                'row': csv_row_number,
                'ticket_id': ticket_id or '(missing)',
                'success': False,
                'errors': errors,
            })
            continue

        valid_tickets.append((csv_row_number, Ticket(
            ticket_id=ticket_id,
            client_name=client_name,
            issue_title=issue_title,
            description=description,
            category=category,
            priority=priority,
            status=status,
            resolution_date=resolution_date,
            assigned_support=assigned_support,
            created_by=importing_user,
        )))

    successful_rows = 0
    failed_rows = len(row_results)
    batch_size = 250

    for batch_start in range(0, len(valid_tickets), batch_size):
        batch = valid_tickets[batch_start:batch_start + batch_size]
        batch_tickets = [ticket for _, ticket in batch]
        try:
            with transaction.atomic():
                Ticket.objects.bulk_create(batch_tickets)
                TicketStatusHistory.objects.bulk_create([
                    TicketStatusHistory(
                        ticket=ticket,
                        previous_status='',
                        new_status=ticket.status,
                        changed_by=importing_user,
                        changed_at=timezone.now(),
                    )
                    for ticket in batch_tickets
                ])
            successful_rows += len(batch_tickets)
            row_results.extend({
                'row': row_number,
                'ticket_id': ticket.ticket_id,
                'success': True,
                'errors': [],
            } for row_number, ticket in batch)
        except (IntegrityError, DataError):
            for row_number, ticket in batch:
                try:
                    with transaction.atomic():
                        ticket._changed_by = importing_user
                        ticket.save()
                except (IntegrityError, DataError, ValidationError) as exc:
                    failed_rows += 1
                    if isinstance(exc, IntegrityError):
                        duplicate_rows += 1
                    reason = str(exc).splitlines()[0] or 'Database validation failed.'
                    row_results.append({
                        'row': row_number,
                        'ticket_id': ticket.ticket_id,
                        'success': False,
                        'errors': [reason],
                    })
                    continue
                successful_rows += 1
                row_results.append({
                    'row': row_number,
                    'ticket_id': ticket.ticket_id,
                    'success': True,
                    'errors': [],
                })

    return {
        'total_rows': total_rows,
        'successful_rows': successful_rows,
        'failed_rows': failed_rows,
        'duplicate_rows': duplicate_rows,
        'row_results': row_results,
    }
