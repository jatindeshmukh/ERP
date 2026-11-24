from django.urls import path

from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.analytics_dashboard, name='dashboard'),
    path('export/', views.analytics_export, name='export'),
]
