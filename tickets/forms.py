from django import forms
from django.contrib.auth.models import User
from django.db.models import Q

from .models import Ticket


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = (
            'client_name',
            'issue_title',
            'description',
            'category',
            'priority',
            'assigned_support',
            'status',
            'resolution_date',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'resolution_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

        is_admin = getattr(user, 'is_superuser', False) or getattr(getattr(user, 'profile', None), 'role', '') == 'Admin'
        is_support = getattr(getattr(user, 'profile', None), 'role', '') == 'Support Executive'
        if not (is_admin or is_support):
            self.fields.pop('assigned_support')
            self.fields.pop('status')
            self.fields.pop('resolution_date')
        else:
            self.fields['assigned_support'].queryset = User.objects.filter(
                Q(is_superuser=True) | Q(profile__role='Support Executive')
            ).distinct()

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        resolution_date = cleaned_data.get('resolution_date')
        if status == Ticket.Status.RESOLVED and not resolution_date:
            self.add_error('resolution_date', 'A resolution date is required when status is Resolved.')
        return cleaned_data
