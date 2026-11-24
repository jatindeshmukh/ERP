from django.urls import path

from . import views

app_name = 'tickets'

urlpatterns = [
    path('', views.ticket_list, name='ticket-list'),
    path('create/', views.ticket_create, name='ticket-create'),
    path('<int:pk>/', views.ticket_detail, name='ticket-detail'),
    path('<int:pk>/edit/', views.ticket_update, name='ticket-update'),
    path('<int:pk>/delete/', views.ticket_delete, name='ticket-delete'),
]
