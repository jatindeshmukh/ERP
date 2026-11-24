from django.test import TestCase
from django.test import Client
from django.contrib.auth.models import User
from django.urls import reverse
import re

from .models import UserProfile


class AccountFlowTests(TestCase):
    def setUp(self):
        self.user_data = {
            'username': 'client-auth',
            'first_name': 'Test',
            'last_name': 'Client',
            'email': 'client-auth@example.com',
            'phone': '1234567890',
            'password1': 'Strong-Pass-984x',
            'password2': 'Strong-Pass-984x',
        }

    def test_signup_creates_client_profile_and_logs_user_in(self):
        response = self.client.post(reverse('accounts:signup'), self.user_data)
        user = User.objects.get(username='client-auth')
        self.assertRedirects(response, reverse('accounts:dashboard'))
        self.assertTrue('_auth_user_id' in self.client.session)
        self.assertEqual(user.profile.role, UserProfile.Role.CLIENT)
        self.assertEqual(user.profile.phone, '1234567890')
        self.assertEqual(user.email, self.user_data['email'])

    def test_signup_cannot_assign_admin_role(self):
        response = self.client.post(
            reverse('accounts:signup'),
            {**self.user_data, 'role': UserProfile.Role.ADMIN},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username='client-auth').profile.role, UserProfile.Role.CLIENT)

    def test_login_rejects_external_next_redirect(self):
        User.objects.create_user(username='login-user', password='Strong-Pass-984x')
        response = self.client.post(
            reverse('accounts:login') + '?next=https://attacker.example/',
            {'username': 'login-user', 'password': 'Strong-Pass-984x'},
        )
        self.assertRedirects(response, reverse('accounts:dashboard'))

    def test_login_allows_safe_relative_next_redirect(self):
        User.objects.create_user(username='login-user', password='Strong-Pass-984x')
        response = self.client.post(
            reverse('accounts:login') + '?next=/tickets/',
            {'username': 'login-user', 'password': 'Strong-Pass-984x'},
        )
        self.assertRedirects(response, '/tickets/')

    def test_logout_requires_csrf_protected_post(self):
        user = User.objects.create_user(username='logout-user', password='Strong-Pass-984x')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('accounts:logout')).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        response = csrf_client.post(reverse('accounts:logout'))
        self.assertEqual(response.status_code, 403)

    def test_invalid_login_does_not_authenticate(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'nobody',
            'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_private_dashboard_requires_authentication(self):
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertRedirects(
            response,
            f'{reverse("accounts:login")}?next={reverse("accounts:dashboard")}',
        )

    def test_stale_login_csrf_token_returns_to_fresh_login_form(self):
        user = User.objects.create_user(
            username='csrf-login-user',
            password='Strong-Pass-984x',
        )
        csrf_client = Client(enforce_csrf_checks=True)
        login_url = reverse('accounts:login') + '?next=/'
        login_page = csrf_client.get(login_url)
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            login_page.content.decode(),
        ).group(1)

        first_login = csrf_client.post(login_url, {
            'username': user.username,
            'password': 'Strong-Pass-984x',
            'csrfmiddlewaretoken': token,
        })
        self.assertEqual(first_login.status_code, 302)

        stale_post = csrf_client.post(login_url, {
            'username': user.username,
            'password': 'Strong-Pass-984x',
            'csrfmiddlewaretoken': token,
        })
        self.assertEqual(stale_post.status_code, 302)
        self.assertEqual(stale_post['Location'], reverse('accounts:dashboard'))

        csrf_client.logout()
        stale_post = csrf_client.post(login_url, {
            'username': user.username,
            'password': 'Strong-Pass-984x',
            'csrfmiddlewaretoken': token,
        })
        self.assertEqual(stale_post.status_code, 302)
        refreshed_login_url = f'{reverse("accounts:login")}?next=%2F'
        self.assertEqual(stale_post['Location'], refreshed_login_url)
        fresh_page = csrf_client.get(stale_post['Location'])
        self.assertContains(fresh_page, 'The login form expired')
