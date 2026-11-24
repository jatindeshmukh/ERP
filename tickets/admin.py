from django.contrib import admin
from .models import Ticket, TicketStatusHistory


class TicketStatusHistoryInline(admin.TabularInline):
    model = TicketStatusHistory
    extra = 0
    readonly_fields = ('previous_status', 'new_status', 'changed_by', 'changed_at')


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('ticket_id', 'client_name', 'issue_title', 'priority', 'status', 'assigned_support', 'created_date')
    list_filter = ('priority', 'category', 'status')
    search_fields = ('ticket_id', 'client_name', 'issue_title')
    readonly_fields = ('ticket_id', 'created_date', 'updated_at')
    inlines = [TicketStatusHistoryInline]

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)
