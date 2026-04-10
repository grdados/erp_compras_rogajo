from pathlib import Path

base = Path('templates/core/relatorios')
base.mkdir(parents=True, exist_ok=True)

(base / 'base.html').write_text('''{% load static core_extras %}
<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{% block title %}Relatorio{% endblock %}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700&display=swap" rel="stylesheet" />
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      body { font-family: 'Lato', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; font-size: 14px; font-weight: 400; }
      .ui-title { font-size: 18px; font-weight: 700; }
      @media print {
        .no-print { display: none !important; }
        body { background: white !important; }
      }
    </style>
  </head>
  <body class="bg-slate-50 text-slate-900">
    <header class="no-print border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div class="flex items-center gap-3">
          <img src="{% static 'img/rogajo-logo.jpeg' %}" class="h-9 w-auto rounded" alt="Rogajo" />
          <div>
            <div class="ui-title">{% block header_title %}Relatorio{% endblock %}</div>
            <div class="text-slate-500">Gerado em {% now 'd/m/Y H:i' %}</div>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="window.print()" class="rounded-md bg-emerald-500 px-3 py-2 text-white hover:bg-emerald-600">Imprimir</button>
          <a href="javascript:history.back()" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-700 hover:bg-slate-100">Voltar</a>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-6xl px-4 py-6">
      {% block content %}{% endblock %}
    </main>
  </body>
</html>
''', encoding='utf-8')

print('OK: relatorios/base.html')
