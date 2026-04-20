from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("licencas", "0010_licenca_status_max_length_32"),
    ]

    operations = [
        migrations.CreateModel(
            name="AtualizacaoSistema",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("versao", models.CharField(max_length=30, unique=True)),
                ("titulo", models.CharField(blank=True, default="", max_length=120)),
                ("descricao", models.TextField(blank=True, default="")),
                ("url_download", models.URLField(blank=True, default="")),
                ("hash_arquivo", models.CharField(blank=True, default="", max_length=120)),
                ("obrigatoria", models.BooleanField(default=False)),
                ("ativa", models.BooleanField(default=True)),
                ("publicada_em", models.DateField(default=django.utils.timezone.localdate)),
            ],
            options={
                "verbose_name": "Atualizacao do Sistema",
                "verbose_name_plural": "Atualizacoes do Sistema",
                "ordering": ["-publicada_em", "-created_at"],
            },
        ),
    ]

