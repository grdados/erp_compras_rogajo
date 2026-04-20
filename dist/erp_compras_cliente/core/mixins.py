from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return getattr(self.request.user, 'effective_role', '') == 'ADMIN'

    def handle_no_permission(self):
        return redirect('core:dashboard')


class GestorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return getattr(self.request.user, 'effective_role', '') in {'ADMIN', 'SUPERVISOR'}

    def handle_no_permission(self):
        return redirect('core:dashboard')
