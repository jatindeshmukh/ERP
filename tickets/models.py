from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
import uuid


class Ticket(models.Model):
    class Priority(models.TextChoices):
        LOW = 'Low', 'Low'
        MEDIUM = 'Medium', 'Medium'
        HIGH = 'High', 'High'
        CRITICAL = 'Critical', 'Critical'

    class Status(models.TextChoices):
        OPEN = 'Open', 'Open'
        IN_PROGRESS = 'In Progress', 'In Progress'
        RESOLVED = 'Resolved', 'Resolved'
        CLOSED = 'Closed', 'Closed'

    class Category(models.TextChoices):
        TECHNICAL = 'Technical', 'Technical'
        BILLING = 'Billing', 'Billing'
        ACCOUNT = 'Account', 'Account'
        SERVICE = 'Service', 'Service'
        OTHER = 'Other', 'Other'

    ticket_id = models.CharField(max_length=30, unique=True, editable=False)
    client_name = models.CharField(max_length=200)
    issue_title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    assigned_support = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tickets',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_tickets',
    )
    created_date = models.DateTimeField(auto_now_add=True)
    resolution_date = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_date']

    def __str__(self):
        return f'{self.ticket_id} - {self.issue_title}'

    def clean(self):
        super().clean()
        if self.status == self.Status.RESOLVED and not self.resolution_date:
            raise ValidationError({'resolution_date': 'A resolution date is required when status is Resolved.'})

    def save(self, *args, **kwargs):
        if not self.ticket_id:
            date_prefix = timezone.now().strftime('%Y%m%d')
            self.ticket_id = f'TKT-{date_prefix}-{uuid.uuid4().hex[:8].upper()}'

        previous_status = None
        if self.pk:
            previous_status = type(self).objects.filter(pk=self.pk).values_list('status', flat=True).first()

        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            changed_by = getattr(self, '_changed_by', None)
            if previous_status is None:
                TicketStatusHistory.objects.create(
                    ticket=self,
                    previous_status='',
                    new_status=self.status,
                    changed_by=changed_by,
                )
            elif previous_status != self.status:
                TicketStatusHistory.objects.create(
                    ticket=self,
                    previous_status=previous_status,
                    new_status=self.status,
                    changed_by=changed_by,
                )


class TicketStatusHistory(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='status_history')
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ticket_status_changes',
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f'{self.ticket.ticket_id}: {self.previous_status or "New"} -> {self.new_status}'
