from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("licencas", "0009_licenca_plano_mensal_e_status_cancelamento_pendente"),
    ]

    operations = [
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
                max_length=32,
            ),
        ),
    ]

