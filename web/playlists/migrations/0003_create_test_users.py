from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_test_users(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    test_users = [
        {'username': 'user1', 'email': 'user1@gmail.com', 'password': 'user1pass'},
        {'username': 'user2', 'email': 'user2@gmail.com', 'password': 'user2pass'},
    ]
    for data in test_users:
        if not User.objects.filter(username=data['username']).exists():
            User.objects.create(
                username=data['username'],
                email=data['email'],
                password=make_password(data['password']),
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('playlists', '0002_playlist_user'),
    ]

    operations = [
        migrations.RunPython(create_test_users, migrations.RunPython.noop),
    ]
