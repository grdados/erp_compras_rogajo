import re
from pathlib import Path

p = Path('templates/core/relatorios/base.html')
text = p.read_text(encoding='utf-8')

new_header = r'''    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto max-w-6xl px-3 py-2">
        <div class="flex items-center gap-3">
          <img src="{% static 'img/rogajo-logo.jpeg' %}" class="h-8 w-auto rounded" alt="Rogajo" />
          <div class="flex-1 text-center">
            <div class="ui-title">{% block header_title %}Relatorio{% endblock %}</div>
          </div>
          <div class="w-[240px] text-right">
            <div class="text-slate-600"><strong>Data Emissao:</strong> {% now 'd/m/Y' %}</div>
          </div>
        </div>
        <div class="mt-1 text-slate-700">
          {% if licenca %}
            {{ licenca.endereco }}, {{ licenca.numero }} - {{ licenca.cidade }}/{{ licenca.uf }} | Contato: {{ licenca.contato }} | {{ licenca.email }}
          {% else %}
            Endereco, Contato, email
          {% endif %}
        </div>
        <div class="mt-2 border-b border-slate-300"></div>
        <div class="no-print mt-2 flex items-center justify-end gap-2">
          <button onclick="window.print()" class="rounded-md bg-emerald-500 px-3 py-2 text-white hover:bg-emerald-600">Imprimir</button>
          <a href="#" onclick="(function(){try{if(window.history.length>1){window.history.back();}else{window.location.href='{{ back_fallback_url|default:'/dashboard/' }}';}}catch(e){window.location.href='{{ back_fallback_url|default:'/dashboard/' }}';}})(); return false;" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-700 hover:bg-slate-100">Voltar</a>
        </div>
      </div>
    </header>'''

text2 = re.sub(r'(?s)\s*<header[^>]*>.*?</header>', new_header, text, count=1)
if text2 == text:
    raise SystemExit('header not replaced')

footer = r'''
    <footer class="border-t border-slate-300 bg-white">
      <div class="mx-auto max-w-6xl px-3 py-2">
        <div class="border-b border-slate-300"></div>
        <div class="mt-2 flex items-center justify-between text-slate-700">
          <div>{% if licenca %}{{ licenca.cidade }} / {{ licenca.uf }}{% else %}Laguna Carapa / MS{% endif %}</div>
          <div class="font-bold">Rogajo Consultoria</div>
          <div class="text-right">Desenvolvido por: GR Dados Businnes Inteligence | WhatsApp (67) 99869-8159</div>
        </div>
      </div>
    </footer>
'''

text2 = text2.replace('\n  </body>', footer + '\n  </body>')

# Ensure print footer fixed and space reserved
text2 = text2.replace('@media print {', '@media print {\n        footer { position: fixed; bottom: 8mm; left: 0; right: 0; }\n        main { padding-bottom: 24mm !important; }')

p.write_text(text2, encoding='utf-8')
print('ok')
