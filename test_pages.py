"""Test all page endpoints with Django test client."""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'classsync.settings'
import django
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS.append('testserver')

from django.test import Client

c = Client()
errors = []

def test_page(client, url, expected=200, label=None):
    label = label or url
    try:
        r = client.get(url)
        status = r.status_code
        if status == expected:
            print(f"  OK  {label}: {status}")
        elif status == 302:
            print(f"  REDIRECT  {label}: {status} -> {r.url}")
        else:
            print(f"  FAIL  {label}: {status} (expected {expected})")
            errors.append(f"{label}: got {status}")
    except Exception as e:
        print(f"  ERROR  {label}: {e}")
        errors.append(f"{label}: {e}")

# ===== ADMIN =====
print("=== Admin Login ===")
r = c.post('/login/', {'username': 'admin_demo', 'password': 'Admin@1234'})
print(f"  Login: {r.status_code}")

print("\n=== Admin Pages ===")
test_page(c, '/admin-panel/')
test_page(c, '/admin-panel/users/')
test_page(c, '/admin-panel/users/create/')
test_page(c, '/admin-panel/departments/')
test_page(c, '/admin-panel/departments/create/')
test_page(c, '/admin-panel/courses/')
test_page(c, '/admin-panel/courses/create/')
test_page(c, '/admin-panel/sections/')
test_page(c, '/admin-panel/sections/create/')
test_page(c, '/admin-panel/timetable/')
test_page(c, '/admin-panel/timetable/create/')
test_page(c, '/admin-panel/config/')
test_page(c, '/admin-panel/absences/')
test_page(c, '/admin-panel/announce/')
test_page(c, '/notifications/')
test_page(c, '/notifications/risk-flags/')
test_page(c, '/notifications/read-all/', expected=302)

# ===== FACULTY =====
c.logout()
print("\n=== Faculty Login ===")
r = c.post('/login/', {'username': 'dr.naseer', 'password': 'Faculty@1234'})
print(f"  Login: {r.status_code}")

print("\n=== Faculty Pages ===")
test_page(c, '/faculty/dashboard/')
test_page(c, '/faculty/timetable/')
test_page(c, '/absence/report/')
test_page(c, '/absence/my/')
test_page(c, '/absence/opt-in/')
test_page(c, '/assignments/faculty/')
test_page(c, '/assignments/post/')
test_page(c, '/absence/history/')
test_page(c, '/notifications/')
test_page(c, '/notifications/risk-flags/')

# Test attendance section dashboard
from core.models import Section
section = Section.objects.filter(faculty__username='dr.naseer').first()
if section:
    test_page(c, f'/attendance/dashboard/{section.pk}/')

# Test submission dashboard
from assignments.models import Assignment
assignment = Assignment.objects.filter(section=section).first()
if assignment:
    test_page(c, f'/assignments/{assignment.pk}/submissions/')

# ===== STUDENT =====
c.logout()
print("\n=== Student Login ===")
r = c.post('/login/', {'username': '23BCS1001', 'password': 'Student@1234'})
print(f"  Login: {r.status_code}")

print("\n=== Student Pages ===")
test_page(c, '/student/dashboard/')
test_page(c, '/student/timetable/')
test_page(c, '/attendance/mark/')
test_page(c, '/attendance/my/')
test_page(c, '/assignments/student/')
test_page(c, '/notifications/')

# Test assignment submission page
if assignment:
    test_page(c, f'/assignments/{assignment.pk}/submit/')

# Summary
print("\n" + "=" * 50)
if errors:
    print(f"FAILURES: {len(errors)}")
    for e in errors:
        print(f"  - {e}")
else:
    print("ALL PAGES PASSED!")
