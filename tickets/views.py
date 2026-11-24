from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import TicketForm
from .models import Ticket


def role_name(user):
    if user.is_superuser:
        return 'Admin'
    return getattr(getattr(user, 'profile', None), 'role', '')


def can_view(user, ticket):
    role = role_name(user)
    return role in ('Admin', 'Support Executive') or ticket.created_by_id == user.id


def can_manage(user, ticket):
    role = role_name(user)
    return role == 'Admin' or (
        role == 'Support Executive' and ticket.assigned_support_id == user.id
    )


@login_required
def ticket_list(request):
    tickets = Ticket.objects.select_related('assigned_support', 'created_by')
    role = role_name(request.user)
    if role not in ('Admin', 'Support Executive'):
        tickets = tickets.filter(created_by=request.user)

    query = request.GET.get('q', '').strip()
    priority = request.GET.get('priority', '').strip()
    category = request.GET.get('category', '').strip()
    status = request.GET.get('status', '').strip()

    if query:
        tickets = tickets.filter(Q(ticket_id__icontains=query) | Q(client_name__icontains=query))
    if priority:
        tickets = tickets.filter(priority=priority)
    if category:
        tickets = tickets.filter(category=category)
    if status:
        tickets = tickets.filter(status=status)

    page_obj = Paginator(tickets, 10).get_page(request.GET.get('page'))
    return render(request, 'tickets/list.html', {
        'page_obj': page_obj,
        'query': query,
        'priority': priority,
        'category': category,
        'status': status,
        'priority_choices': Ticket.Priority.choices,
        'category_choices': Ticket.Category.choices,
        'status_choices': Ticket.Status.choices,
    })


@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related('assigned_support', 'created_by'),
        pk=pk,
    )
    if not can_view(request.user, ticket):
        messages.error(request, 'You do not have permission to view this ticket.')
        return redirect('tickets:ticket-list')
    return render(request, 'tickets/detail.html', {
        'ticket': ticket,
        'can_manage': can_manage(request.user, ticket),
        'can_delete': role_name(request.user) == 'Admin',
    })


@login_required
def ticket_create(request):
    if request.method == 'POST':
        form = TicketForm(request.POST, user=request.user)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.created_by = request.user
            ticket._changed_by = request.user
            ticket.save()
            messages.success(request, f'Ticket {ticket.ticket_id} created successfully.')
            return redirect('tickets:ticket-detail', pk=ticket.pk)
    else:
        form = TicketForm(user=request.user)
    return render(request, 'tickets/form.html', {'form': form, 'action': 'Create'})


@login_required
def ticket_update(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if not can_manage(request.user, ticket):
        messages.error(request, 'You do not have permission to modify this ticket.')
        return redirect('tickets:ticket-detail', pk=pk)
    if request.method == 'POST':
        form = TicketForm(request.POST, instance=ticket, user=request.user)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket._changed_by = request.user
            ticket.save()
            messages.success(request, f'Ticket {ticket.ticket_id} updated successfully.')
            return redirect('tickets:ticket-detail', pk=ticket.pk)
    else:
        form = TicketForm(instance=ticket, user=request.user)
    return render(request, 'tickets/form.html', {
        'form': form,
        'action': 'Update',
        'ticket': ticket,
    })


@login_required
@require_POST
def ticket_delete(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if role_name(request.user) != 'Admin':
        messages.error(request, 'Only administrators can delete tickets.')
        return redirect('tickets:ticket-detail', pk=pk)
    ticket_id = ticket.ticket_id
    ticket.delete()
    messages.success(request, f'Ticket {ticket_id} deleted successfully.')
    return redirect('tickets:ticket-list')
