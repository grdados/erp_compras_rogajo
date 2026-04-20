from django.db import migrations


def forwards(apps, schema_editor):
    ContaPagar = apps.get_model('financeiro', 'ContaPagar')

    ContaPagar.objects.filter(status='PENDENTE').update(status='A_PAGAR')
    ContaPagar.objects.filter(status='QUITADO').update(status='PAGO')


def backwards(apps, schema_editor):
    ContaPagar = apps.get_model('financeiro', 'ContaPagar')

    ContaPagar.objects.filter(status='A_PAGAR').update(status='PENDENTE')
    ContaPagar.objects.filter(status='PAGO').update(status='QUITADO')


class Migration(migrations.Migration):
    dependencies = [
        ('financeiro', '0004_contapagar_fornecedor_contapagar_safra_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
