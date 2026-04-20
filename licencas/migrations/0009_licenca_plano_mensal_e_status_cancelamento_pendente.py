from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("licencas", "0008_licenca_asaas_bank_slip_url_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="licenca",
            name="plano",
            field=models.CharField(
                blank=True,
                choices=[("MENSAL", "Mensal"), ("SEMESTRAL", "Semestral"), ("ANUAL", "Anual")],
                default="",
                max_length=15,
            ),
        ),
        migrations.AlterField(
            model_name="licenca",
            name="status",
            field=models.CharField(
                choices=[
                    ("ATIVA", "Ativa"),
                    ("AGUARDANDO_CONFIRMACAO", "Aguardando confirmacao"),
                    ("CANCELAMENTO_REMOTO_PENDENTE", "Cancelamento remoto pendente"),
                    ("INADIMPLENTE", "Inadimplente"),
                    ("EXPIRADA", "Expirada"),
                    ("CANCELADA", "Cancelada"),
                ],
                default="EXPIRADA",
                max_length=24,
            ),
        ),
    ]

