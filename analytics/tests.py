from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from datetime import date, datetime
from django.utils import timezone

from tickets.models import Ticket


class AnalyticsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='analytics-admin',
            email='analytics@example.com',
            password='test-password-123',
        )

    def test_empty_database_is_safe(self):
        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_empty'])
        self.assertEqual(response.context['total_tickets'], 0)
        self.assertContains(response, 'No ticket records found')

    def test_admin_sees_dynamic_metrics(self):
        ticket = Ticket(
            client_name='Test Client',
            issue_title='Database outage',
            description='Database unavailable',
            category=Ticket.Category.TECHNICAL,
            priority=Ticket.Priority.CRITICAL,
            status=Ticket.Status.RESOLVED,
            resolution_date='2026-11-24',
            created_by=self.admin,
        )
        ticket._changed_by = self.admin
        ticket.save()
        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_tickets'], 1)
        self.assertEqual(response.context['resolved_tickets'], 1)
        self.assertEqual(response.context['critical_issues'], 1)

    def test_non_admin_receives_permission_denied(self):
        client = User.objects.create_user(
            username='analytics-client',
            password='test-password-123',
        )
        self.client.login(username='analytics-client', password='test-password-123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_detects_repeated_issue_by_same_client(self):
        for ticket_id, title in (
            ('TKT-REPEAT-1', 'Cannot post invoice'),
            ('TKT-REPEAT-2', ' cannot  post invoice '),
        ):
            ticket = Ticket(
                ticket_id=ticket_id,
                client_name='Acme',
                issue_title=title,
                description='Invoice posting issue',
                category=Ticket.Category.BILLING,
                priority=Ticket.Priority.MEDIUM,
                created_by=self.admin,
            )
            ticket._changed_by = self.admin
            ticket.save()

        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['repeated_client_issues'], [{
            'client_name': 'Acme',
            'issue_title': 'Cannot post invoice',
            'count': 2,
        }])

    def test_csv_export_contains_ticket_data_and_repeated_issue_insight(self):
        for ticket_id in ('TKT-EXPORT-1', 'TKT-EXPORT-2'):
            ticket = Ticket(
                ticket_id=ticket_id,
                client_name='Acme',
                issue_title='Cannot post invoice',
                description='Invoice posting issue',
                category=Ticket.Category.BILLING,
                priority=Ticket.Priority.MEDIUM,
                created_by=self.admin,
            )
            ticket._changed_by = self.admin
            ticket.save()

        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:export'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8')
        self.assertIn('TKT-EXPORT-1', content)
        self.assertIn('Repeated client issue', content)
        self.assertIn('Same issue reported repeatedly by this client', content)

    def test_empty_csv_export_has_headers(self):
        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Report Type,Ticket ID,Client Name', response.content.decode('utf-8'))

    def test_date_range_filters_dashboard_and_csv_export(self):
        old_ticket = Ticket(
            ticket_id='TKT-OLD-DATE',
            client_name='Old Client',
            issue_title='Old issue',
            description='Old description',
            category=Ticket.Category.OTHER,
            priority=Ticket.Priority.LOW,
            created_by=self.admin,
        )
        old_ticket.save()
        recent_ticket = Ticket(
            ticket_id='TKT-RECENT-DATE',
            client_name='Recent Client',
            issue_title='Recent issue',
            description='Recent description',
            category=Ticket.Category.OTHER,
            priority=Ticket.Priority.LOW,
            created_by=self.admin,
        )
        recent_ticket.save()
        Ticket.objects.filter(pk=old_ticket.pk).update(
            created_date=timezone.make_aware(datetime(2026, 1, 15, 12, 0))
        )
        Ticket.objects.filter(pk=recent_ticket.pk).update(
            created_date=timezone.make_aware(datetime(2026, 2, 15, 12, 0))
        )

        self.client.login(username='analytics-admin', password='test-password-123')
        params = {'start_date': '2026-01-01', 'end_date': '2026-01-31'}
        dashboard = self.client.get(reverse('analytics:dashboard'), params)
        self.assertEqual(dashboard.context['total_tickets'], 1)
        exported = self.client.get(reverse('analytics:export'), params)
        csv_content = exported.content.decode('utf-8')
        self.assertIn('TKT-OLD-DATE', csv_content)
        self.assertNotIn('TKT-RECENT-DATE', csv_content)

    def test_bi_insights_use_database_records(self):
        support = User.objects.create_user(username='bi-support')
        support.profile.role = 'Support Executive'
        support.profile.save()
        test_rows = [
            ('TKT-BI-1', Ticket.Category.TECHNICAL, Ticket.Priority.LOW, Ticket.Status.OPEN, date(2026, 1, 2), None),
            ('TKT-BI-2', Ticket.Category.TECHNICAL, Ticket.Priority.HIGH, Ticket.Status.IN_PROGRESS, date(2026, 1, 3), None),
            ('TKT-BI-3', Ticket.Category.BILLING, Ticket.Priority.CRITICAL, Ticket.Status.RESOLVED, date(2026, 2, 1), date(2026, 2, 5)),
        ]
        for ticket_id, category, priority, status, created_day, resolution_day in test_rows:
            ticket = Ticket(
                ticket_id=ticket_id,
                client_name='Insight Client',
                issue_title='ERP outage',
                description='ERP issue',
                category=category,
                priority=priority,
                status=status,
                resolution_date=resolution_day,
                assigned_support=support,
                created_by=self.admin,
            )
            ticket.save()
            Ticket.objects.filter(pk=ticket.pk).update(
                created_date=timezone.make_aware(datetime.combine(created_day, datetime.min.time()))
            )

        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.context['total_tickets'], 3)
        self.assertEqual(response.context['highest_complaint_category'], {
            'name': Ticket.Category.TECHNICAL,
            'count': 2,
        })
        self.assertEqual(response.context['executive_highest_pending']['name'], 'bi-support')
        self.assertEqual(response.context['executive_highest_pending']['pending'], 2)
        self.assertEqual(response.context['busiest_month'], {'label': '2026-01', 'count': 2})
        self.assertEqual(response.context['longest_resolution_priority']['name'], Ticket.Priority.CRITICAL)
        self.assertEqual(response.context['repeated_client_issues'][0]['count'], 3)

    def test_profile_admin_can_access_and_client_cannot_download_report(self):
        profile_admin = User.objects.create_user(username='profile-admin', password='test-password-123')
        profile_admin.profile.role = 'Admin'
        profile_admin.profile.save()
        self.client.login(username='profile-admin', password='test-password-123')
        self.assertEqual(self.client.get(reverse('analytics:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('analytics:export')).status_code, 200)

        client_user = User.objects.create_user(username='report-client', password='test-password-123')
        self.client.force_login(client_user)
        self.assertEqual(self.client.get(reverse('analytics:dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('analytics:export')).status_code, 403)

    def test_csv_export_escapes_spreadsheet_formulas(self):
        ticket = Ticket(
            client_name='Acme',
            issue_title='=HYPERLINK("https://example.invalid")',
            description='Imported user-controlled text',
            category=Ticket.Category.OTHER,
            priority=Ticket.Priority.LOW,
            created_by=self.admin,
        )
        ticket.save()
        self.client.login(username='analytics-admin', password='test-password-123')
        response = self.client.get(reverse('analytics:export'))
        content = response.content.decode('utf-8')
        self.assertIn('\'=HYPERLINK', content)
        self.assertNotIn(',=HYPERLINK', content)
