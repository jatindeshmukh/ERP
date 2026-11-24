from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from tickets.models import Ticket


class TicketCSVImportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='import-admin',
            email='import-admin@example.com',
            password='test-password-123',
        )
        existing = Ticket(
            ticket_id='TKT-EXISTING',
            client_name='Existing Client',
            issue_title='Existing issue',
            description='Existing description',
            category=Ticket.Category.ACCOUNT,
            priority=Ticket.Priority.LOW,
            status=Ticket.Status.OPEN,
            created_by=self.admin,
        )
        existing._changed_by = self.admin
        existing.save()

    def upload(self, content):
        return SimpleUploadedFile(
            'tickets.csv',
            content.encode('utf-8'),
            content_type='text/csv',
        )

    def test_admin_imports_valid_rows_and_rejects_bad_rows_individually(self):
        csv_content = (
            'Ticket ID,Client Name,Issue Title,Description,Category,Priority,Status,Resolution Date,Assigned Support\n'
            'TKT-EXISTING,Existing Client,Duplicate issue,Description,Account,Low,Open,,\n'
            'TKT-NEW-001,New Client,Valid issue,Description,Technical,High,Open,,\n'
            'TKT-NEW-001,New Client,Duplicate issue,Description,Technical,High,Open,,\n'
            'TKT-NEW-002,Bad Client,Bad priority,Description,Technical,Extreme,Open,,\n'
        )
        self.client.login(username='import-admin', password='test-password-123')
        response = self.client.post(
            reverse('imports:dashboard'),
            {'file': self.upload(csv_content)},
        )
        self.assertEqual(response.status_code, 200)
        summary = response.context['summary']
        self.assertEqual(summary['total_rows'], 4)
        self.assertEqual(summary['successful_rows'], 1)
        self.assertEqual(summary['failed_rows'], 3)
        self.assertEqual(summary['duplicate_rows'], 2)
        self.assertTrue(Ticket.objects.filter(ticket_id='TKT-NEW-001').exists())
        self.assertFalse(Ticket.objects.filter(ticket_id='TKT-NEW-002').exists())

    def test_missing_required_column_is_rejected(self):
        csv_content = (
            'Ticket ID,Client Name,Issue Title,Description,Category,Priority\n'
            'TKT-1,Client,Issue,Description,Technical,Low\n'
        )
        self.client.login(username='import-admin', password='test-password-123')
        response = self.client.post(
            reverse('imports:dashboard'),
            {'file': self.upload(csv_content)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('Status', response.context['form'].errors['file'][0])

    def test_header_whitespace_does_not_prevent_import(self):
        csv_content = (
            ' Ticket ID , Client Name,Issue Title,Description,Category,Priority,Status \n'
            'TKT-TRIMMED-001,Trimmed Client,Valid issue,Description,Technical,High,Open\n'
        )
        self.client.login(username='import-admin', password='test-password-123')
        response = self.client.post(
            reverse('imports:dashboard'),
            {'file': self.upload(csv_content)},
        )
        self.assertEqual(response.status_code, 200)
        summary = response.context['summary']
        self.assertEqual(summary['successful_rows'], 1)
        self.assertTrue(Ticket.objects.filter(ticket_id='TKT-TRIMMED-001').exists())
        self.assertTrue(any(
            row['success'] and row['ticket_id'] == 'TKT-TRIMMED-001'
            for row in summary['row_results']
        ))

    def test_non_admin_cannot_access_import_page(self):
        client = User.objects.create_user(
            username='import-client',
            password='test-password-123',
        )
        self.client.login(username='import-client', password='test-password-123')
        response = self.client.get(reverse('imports:dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_valid_support_username_is_assigned_and_status_history_is_created(self):
        support = User.objects.create_user(username='assigned-support')
        support.profile.role = 'Support Executive'
        support.profile.save()
        content = (
            'Ticket ID,Client Name,Issue Title,Description,Category,Priority,Status,Assigned Support\n'
            'TKT-ASSIGNED,Client,Issue,Description,Technical,High,Open,assigned-support\n'
        )
        self.client.login(username='import-admin', password='test-password-123')
        response = self.client.post(
            reverse('imports:dashboard'),
            {'file': self.upload(content)},
        )
        ticket = Ticket.objects.get(ticket_id='TKT-ASSIGNED')
        self.assertEqual(response.context['summary']['successful_rows'], 1)
        self.assertEqual(ticket.assigned_support, support)
        history = ticket.status_history.get()
        self.assertEqual(history.changed_by, self.admin)

    def test_client_username_cannot_be_used_as_support_assignment(self):
        User.objects.create_user(username='ordinary-client')
        content = (
            'Ticket ID,Client Name,Issue Title,Description,Category,Priority,Status,Assigned Support\n'
            'TKT-NON-SUPPORT,Client,Issue,Description,Technical,High,Open,ordinary-client\n'
        )
        self.client.login(username='import-admin', password='test-password-123')
        response = self.client.post(
            reverse('imports:dashboard'),
            {'file': self.upload(content)},
        )
        summary = response.context['summary']
        self.assertEqual(summary['successful_rows'], 0)
        self.assertIn('Assigned Support username does not exist.', summary['row_results'][0]['errors'])

    def test_non_csv_file_is_rejected(self):
        self.client.login(username='import-admin', password='test-password-123')
        upload = SimpleUploadedFile('tickets.xlsx', b'not a csv', content_type='text/csv')
        response = self.client.post(reverse('imports:dashboard'), {'file': upload})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Only .csv files are accepted.', response.context['form'].errors['file'][0])

    def test_csv_import_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        upload = self.upload(
            'Ticket ID,Client Name,Issue Title,Description,Category,Priority,Status\n'
            'TKT-CSRF,Client,Issue,Description,Technical,Low,Open\n'
        )
        response = csrf_client.post(reverse('imports:dashboard'), {'file': upload})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Ticket.objects.filter(ticket_id='TKT-CSRF').exists())
