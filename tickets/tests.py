from django.test import TestCase
from django.test import Client
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.urls import reverse
from unittest.mock import patch

from .models import Ticket, TicketStatusHistory


class TicketManagementTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            username='client1', password='test-password-123',
            email='client1@example.com',
        )
        self.support_user = User.objects.create_user(
            username='support1', password='test-password-123',
        )
        self.support_user.profile.role = 'Support Executive'
        self.support_user.profile.save()

        self.admin_user = User.objects.create_superuser(
            username='admin1',
            email='admin1@example.com',
            password='test-password-123',
        )

    def create_ticket(self):
        ticket = Ticket(
            client_name='Acme ERP',
            issue_title='Unable to post invoice',
            description='Invoice posting fails.',
            category=Ticket.Category.TECHNICAL,
            priority=Ticket.Priority.HIGH,
            created_by=self.client_user,
        )
        ticket._changed_by = self.client_user
        ticket.save()
        return ticket

    def test_create_records_initial_status_history(self):
        ticket = self.create_ticket()
        self.assertTrue(ticket.ticket_id.startswith('TKT-'))
        history = TicketStatusHistory.objects.get(ticket=ticket)
        self.assertEqual(history.previous_status, '')
        self.assertEqual(history.new_status, Ticket.Status.OPEN)
        self.assertEqual(history.changed_by, self.client_user)

    def test_status_change_records_history_and_resolution_requires_date(self):
        ticket = self.create_ticket()
        ticket.status = Ticket.Status.RESOLVED
        ticket._changed_by = self.support_user
        with self.assertRaises(ValidationError):
            ticket.save()

        ticket.status = Ticket.Status.RESOLVED
        ticket.resolution_date = '2026-11-24'
        ticket._changed_by = self.support_user
        ticket.save()
        history = TicketStatusHistory.objects.filter(ticket=ticket).first()
        self.assertEqual(history.previous_status, Ticket.Status.OPEN)
        self.assertEqual(history.new_status, Ticket.Status.RESOLVED)
        self.assertEqual(history.changed_by, self.support_user)

    def test_client_cannot_view_another_clients_ticket(self):
        ticket = self.create_ticket()
        other = User.objects.create_user(
            username='client2', password='test-password-123',
        )
        self.client.login(username='client2', password='test-password-123')
        response = self.client.get(reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertRedirects(response, reverse('tickets:ticket-list'))

    def test_owner_can_read_ticket_detail(self):
        ticket = self.create_ticket()
        self.client.login(username='client1', password='test-password-123')
        response = self.client.get(reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ticket.ticket_id)

    def test_support_can_search_and_filter_tickets(self):
        ticket = self.create_ticket()
        self.client.login(username='support1', password='test-password-123')
        response = self.client.get(reverse('tickets:ticket-list'), {
            'q': 'Acme',
            'priority': 'High',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ticket.ticket_id)

    def test_client_can_create_ticket(self):
        self.client.login(username='client1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-create'), {
            'client_name': 'Acme ERP',
            'issue_title': 'Login problem',
            'description': 'Cannot access ERP.',
            'category': Ticket.Category.ACCOUNT,
            'priority': Ticket.Priority.HIGH,
        })
        ticket = Ticket.objects.get(issue_title='Login problem')
        self.assertRedirects(response, reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertEqual(ticket.created_by, self.client_user)
        self.assertEqual(ticket.status, Ticket.Status.OPEN)
        self.assertEqual(ticket.status_history.count(), 1)

    def test_support_can_update_assigned_ticket_and_records_history(self):
        ticket = self.create_ticket()
        ticket.assigned_support = self.support_user
        ticket.save()
        self.client.login(username='support1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-update', args=[ticket.pk]), {
            'client_name': ticket.client_name,
            'issue_title': ticket.issue_title,
            'description': ticket.description,
            'category': ticket.category,
            'priority': ticket.priority,
            'assigned_support': self.support_user.pk,
            'status': Ticket.Status.IN_PROGRESS,
            'resolution_date': '',
        })
        ticket.refresh_from_db()
        self.assertRedirects(response, reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertEqual(ticket.status, Ticket.Status.IN_PROGRESS)
        history = ticket.status_history.first()
        self.assertEqual(history.previous_status, Ticket.Status.OPEN)
        self.assertEqual(history.new_status, Ticket.Status.IN_PROGRESS)
        self.assertEqual(history.changed_by, self.support_user)

    def test_support_cannot_modify_ticket_assigned_to_another_executive(self):
        ticket = self.create_ticket()
        other_support = User.objects.create_user(username='support2', password='test-password-123')
        other_support.profile.role = 'Support Executive'
        other_support.profile.save()
        ticket.assigned_support = other_support
        ticket.save()
        self.client.login(username='support1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-update', args=[ticket.pk]), {
            'client_name': 'Tampered',
            'issue_title': ticket.issue_title,
            'description': ticket.description,
            'category': ticket.category,
            'priority': ticket.priority,
            'assigned_support': self.support_user.pk,
            'status': Ticket.Status.OPEN,
        })
        ticket.refresh_from_db()
        self.assertRedirects(response, reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertEqual(ticket.client_name, 'Acme ERP')
        self.assertEqual(ticket.assigned_support, other_support)

    def test_admin_can_assign_update_and_delete_ticket(self):
        ticket = self.create_ticket()
        self.client.login(username='admin1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-update', args=[ticket.pk]), {
            'client_name': ticket.client_name,
            'issue_title': 'Updated issue',
            'description': ticket.description,
            'category': ticket.category,
            'priority': Ticket.Priority.CRITICAL,
            'assigned_support': self.support_user.pk,
            'status': Ticket.Status.OPEN,
            'resolution_date': '',
        })
        ticket.refresh_from_db()
        self.assertRedirects(response, reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertEqual(ticket.assigned_support, self.support_user)
        self.assertEqual(ticket.issue_title, 'Updated issue')

        response = self.client.post(reverse('tickets:ticket-delete', args=[ticket.pk]))
        self.assertRedirects(response, reverse('tickets:ticket-list'))
        self.assertFalse(Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertFalse(TicketStatusHistory.objects.filter(ticket_id=ticket.pk).exists())

    def test_resolved_ticket_requires_resolution_date_in_form(self):
        ticket = self.create_ticket()
        ticket.assigned_support = self.support_user
        ticket.save()
        self.client.login(username='support1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-update', args=[ticket.pk]), {
            'client_name': ticket.client_name,
            'issue_title': ticket.issue_title,
            'description': ticket.description,
            'category': ticket.category,
            'priority': ticket.priority,
            'assigned_support': self.support_user.pk,
            'status': Ticket.Status.RESOLVED,
            'resolution_date': '',
        })
        ticket.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ticket.status, Ticket.Status.OPEN)
        self.assertContains(response, 'A resolution date is required')

    def test_ticket_and_history_are_atomic(self):
        with patch(
            'tickets.models.TicketStatusHistory.objects.create',
            side_effect=RuntimeError('history insert failed'),
        ):
            with self.assertRaises(RuntimeError):
                self.create_ticket()
        self.assertFalse(Ticket.objects.filter(client_name='Acme ERP').exists())

    def test_search_treats_sql_payload_as_plain_text(self):
        ticket = self.create_ticket()
        self.client.login(username='support1', password='test-password-123')
        response = self.client.get(reverse('tickets:ticket-list'), {'q': "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ticket.ticket_id)

    def test_ticket_post_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.client_user)
        response = csrf_client.post(reverse('tickets:ticket-create'), {
            'client_name': 'Acme ERP',
            'issue_title': 'CSRF check',
            'description': 'Should not save.',
            'category': Ticket.Category.ACCOUNT,
            'priority': Ticket.Priority.LOW,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Ticket.objects.filter(issue_title='CSRF check').exists())

    def test_support_cannot_delete_ticket(self):
        ticket = self.create_ticket()
        self.client.login(username='support1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-delete', args=[ticket.pk]))
        self.assertRedirects(response, reverse('tickets:ticket-detail', args=[ticket.pk]))
        self.assertTrue(Ticket.objects.filter(pk=ticket.pk).exists())

    def test_invalid_category_is_rejected_by_ticket_form(self):
        self.client.login(username='client1', password='test-password-123')
        response = self.client.post(reverse('tickets:ticket-create'), {
            'client_name': 'Acme ERP',
            'issue_title': 'Invalid category',
            'description': 'Should not save.',
            'category': 'arbitrary SQL payload',
            'priority': Ticket.Priority.HIGH,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ticket.objects.filter(issue_title='Invalid category').exists())
