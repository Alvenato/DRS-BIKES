from flask import Flask, render_template, request, redirect, url_for, session
import datetime
import os
import random
import string

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

import sheets_db as db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

NOME_CONSUMIDOR_PADRAO = "CONSUMIDOR NAO IDENTIFICADO"

# Nomes das abas (devem bater exatamente com os nomes na planilha, incl. maiúsculas)
SHEET_PRODUTOS = "produtos"
SHEET_VENDAS = "Vendas"
SHEET_CLIENTES = "cliente"
SHEET_VENDA_ITENS = "vendas_itens"
SHEET_USUARIOS = "Usuarios"


# ─────────────────────────── AUTENTICAÇÃO ──────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        usuarios = db.get_all_rows(SHEET_USUARIOS)
        user = next(
            (u for u in usuarios
             if str(u.get('Usuario', '')).strip() == username
             and str(u.get('Senha', '')).strip() == password),
            None
        )

        if user:
            session['logged_in'] = True
            session['username'] = username
            # Coluna opcional: se não existir na planilha, assume-se que NÃO é primeiro acesso.
            primeiro_acesso = str(user.get('primeiro_acesso', '0')).strip()
            if primeiro_acesso == '1':
                return redirect(url_for('alterar_senha'))
            return redirect(url_for('admin'))

        return render_template('login.html', error="Usuário ou senha inválidos.")
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/alterar_senha', methods=['GET', 'POST'])
def alterar_senha():
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nova = request.form.get('nova_senha', '').strip()
        confirmar = request.form.get('confirmar_senha', '').strip()

        if not nova or len(nova) < 4:
            return render_template('alterar_senha.html', error="A senha deve ter pelo menos 4 caracteres.")
        if nova != confirmar:
            return render_template('alterar_senha.html', error="As senhas não coincidem.")
        if nova == 'admin':
            return render_template('alterar_senha.html', error="Escolha uma senha diferente da senha padrão.")

        db.update_cell(SHEET_USUARIOS, 'Usuario', session['username'], 'Senha', nova)
        db.update_cell(SHEET_USUARIOS, 'Usuario', session['username'], 'primeiro_acesso', '0')
        return redirect(url_for('admin'))

    return render_template('alterar_senha.html')


# ─────────────────────────── ADMIN / VENDAS ────────────────────────────────

@app.route('/admin')
def admin():
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    dia = request.args.get('dia', '').strip()
    mes = request.args.get('mes', '').strip()
    ano = request.args.get('ano', '').strip()

    vendas = db.normalize_rows(db.get_all_rows(SHEET_VENDAS), int_fields=['id'], float_fields=['valor'])

    def passa_filtro(v):
        data = str(v.get('data', ''))
        if dia and data[0:2] != dia:
            return False
        if mes and data[3:5] != mes:
            return False
        if ano and data[6:10] != ano:
            return False
        return True

    vendas = [v for v in vendas if passa_filtro(v)]
    vendas.sort(key=lambda v: v.get('id', 0), reverse=True)

    total_vendas = len(vendas)
    faturamento_total = sum(v.get('valor', 0.0) for v in vendas)
    faturamento_dinheiro = sum(v.get('valor', 0.0) for v in vendas if (v.get('forma_pagamento') or '') == 'Dinheiro')
    faturamento_pix = sum(v.get('valor', 0.0) for v in vendas if (v.get('forma_pagamento') or '') == 'Pix')
    faturamento_credito = sum(v.get('valor', 0.0) for v in vendas if (v.get('forma_pagamento') or '') == 'Credito')
    faturamento_debito = sum(v.get('valor', 0.0) for v in vendas if (v.get('forma_pagamento') or '') == 'Debito')

    return render_template(
        'admin.html',
        vendas=vendas,
        active_page='admin',
        total_vendas=total_vendas,
        faturamento_total=faturamento_total,
        faturamento_dinheiro=faturamento_dinheiro,
        faturamento_pix=faturamento_pix,
        faturamento_credito=faturamento_credito,
        faturamento_debito=faturamento_debito,
    )


@app.route('/remover_venda/<int:id>', methods=['POST'])
def remover_venda(id):
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    itens = db.normalize_rows(db.get_all_rows(SHEET_VENDA_ITENS), int_fields=['id', 'venda_id'])
    for item in itens:
        if item.get('venda_id') == id:
            db.delete_row(SHEET_VENDA_ITENS, 'id', item.get('id'))
    db.delete_row(SHEET_VENDAS, 'id', id)
    return redirect(url_for('admin'))


@app.route('/emitir_cupom/<int:id>')
def emitir_cupom(id):
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    vendas = db.normalize_rows(db.get_all_rows(SHEET_VENDAS), int_fields=['id'], float_fields=['valor'])
    venda = next((v for v in vendas if v.get('id') == id), None)
    if not venda:
        return "Venda não encontrada.", 404
    venda = dict(venda)
    venda['chave_nfce'] = str(venda.get('chave_nfce') or '')

    clientes_rows = db.get_all_rows(SHEET_CLIENTES)
    cli = next((c for c in clientes_rows if c.get('nome') == venda.get('cliente')), None)
    venda['telefone'] = cli.get('telefone') if cli else ''
    venda['whatsapp'] = cli.get('whatsapp') if cli else ''

    itens = db.normalize_rows(
        db.get_all_rows(SHEET_VENDA_ITENS),
        int_fields=['id', 'venda_id', 'quantidade'],
        float_fields=['preco_unitario'],
    )
    itens = [i for i in itens if i.get('venda_id') == id]

    return render_template('modelo_nf.html', venda=venda, itens=itens)


# ─────────────────────────── FRENTE DE CAIXA ───────────────────────────────

def _produtos_disponiveis():
    produtos = db.normalize_rows(
        db.get_all_rows(SHEET_PRODUTOS), int_fields=['id', 'quantidade'], float_fields=['preco']
    )
    produtos = [p for p in produtos if p.get('quantidade', 0) > 0]
    produtos.sort(key=lambda p: p.get('nome', ''))
    return produtos


def _clientes_cadastrados():
    clientes = db.get_all_rows(SHEET_CLIENTES)
    clientes = [c for c in clientes if c.get('nome') and c.get('nome') != NOME_CONSUMIDOR_PADRAO]
    clientes.sort(key=lambda c: c.get('nome', ''))
    return clientes


@app.route('/caixa')
def caixa():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    return render_template(
        'caixa.html', produtos=_produtos_disponiveis(), clientes=_clientes_cadastrados(), active_page='caixa'
    )


@app.route('/registrar_venda', methods=['POST'])
def registrar_venda():
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    cliente_nome = request.form.get('nome_cliente', NOME_CONSUMIDOR_PADRAO)
    forma_pagamento = request.form.get('forma_pagamento', '')
    nomes_produtos = request.form.getlist('produtos[]')
    quantidades = request.form.getlist('qtd[]')
    precos = request.form.getlist('preco[]')

    if not nomes_produtos or all(n == '' for n in nomes_produtos):
        return render_template(
            'caixa.html', produtos=_produtos_disponiveis(), clientes=_clientes_cadastrados(),
            active_page='caixa', error="Selecione ao menos um produto.",
        )

    chave_nfce = ''.join(random.choices(string.digits, k=44))

    try:
        total_venda = sum(int(q) * float(p.replace(',', '.')) for q, p in zip(quantidades, precos))
    except (ValueError, ZeroDivisionError):
        total_venda = 0.0

    novo_id = db.next_id(SHEET_VENDAS)
    db.append_row(SHEET_VENDAS, ['id', 'cliente', 'data', 'chave_nfce', 'valor', 'forma_pagamento'], {
        'id': novo_id,
        'cliente': cliente_nome,
        'data': datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        'chave_nfce': chave_nfce,
        'valor': total_venda,
        'forma_pagamento': forma_pagamento,
    })

    produtos_atuais = db.get_all_rows(SHEET_PRODUTOS)

    for i in range(len(nomes_produtos)):
        nome_prod = nomes_produtos[i]
        if not nome_prod:
            continue

        try:
            qtd = int(quantidades[i])
            preco = float(precos[i].replace(',', '.'))
        except (ValueError, TypeError, IndexError):
            continue

        item_id = db.next_id(SHEET_VENDA_ITENS)
        db.append_row(SHEET_VENDA_ITENS, ['id', 'venda_id', 'produto_nome', 'quantidade', 'preco_unitario'], {
            'id': item_id,
            'venda_id': novo_id,
            'produto_nome': nome_prod,
            'quantidade': qtd,
            'preco_unitario': preco,
        })

        produto_atual = next((p for p in produtos_atuais if p.get('nome') == nome_prod), None)
        if produto_atual:
            estoque_atual = db.to_int(produto_atual.get('quantidade'))
            novo_estoque = max(estoque_atual - qtd, 0)
            db.update_cell(SHEET_PRODUTOS, 'id', produto_atual.get('id'), 'quantidade', novo_estoque)

    return redirect(url_for('emitir_cupom', id=novo_id))


# ─────────────────────────── CLIENTES ──────────────────────────────────────

@app.route('/cliente', methods=['GET', 'POST'])
def cliente():
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip()
        telefone = request.form.get('telefone', '').strip()
        whatsapp = request.form.get('whatsapp', '').strip()
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip()
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        uf = request.form.get('uf', '').strip()
        data_nasc = request.form.get('data_nasc', '').strip()
        genero = request.form.get('genero', '').strip()

        if nome:
            novo_id = db.next_id(SHEET_CLIENTES)
            db.append_row(
                SHEET_CLIENTES,
                ['id', 'nome', 'email', 'telefone', 'whatsapp', 'cep', 'logradouro', 'numero',
                 'complemento', 'bairro', 'cidade', 'uf', 'data_nasc', 'genero'],
                {
                    'id': novo_id, 'nome': nome, 'email': email, 'telefone': telefone, 'whatsapp': whatsapp,
                    'cep': cep, 'logradouro': logradouro, 'numero': numero, 'complemento': complemento,
                    'bairro': bairro, 'cidade': cidade, 'uf': uf, 'data_nasc': data_nasc, 'genero': genero,
                },
            )

        return redirect(url_for('cliente'))

    return render_template('cliente.html', clientes=_clientes_cadastrados(), active_page='cliente')


@app.route('/cliente/<int:cliente_id>/excluir', methods=['POST'])
def excluir_cliente(cliente_id):
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    db.delete_row(SHEET_CLIENTES, 'id', cliente_id)
    return redirect(url_for('cliente'))


@app.route('/cliente/<int:cliente_id>/editar', methods=['POST'])
def editar_cliente(cliente_id):
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    campos = ['id', 'nome', 'email', 'telefone', 'whatsapp', 'cep', 'logradouro',
              'numero', 'complemento', 'bairro', 'cidade', 'uf', 'data_nasc', 'genero']
    dados = {c: request.form.get(c, '').strip() for c in campos}
    dados['id'] = cliente_id
    db.delete_row(SHEET_CLIENTES, 'id', cliente_id)
    db.append_row(SHEET_CLIENTES, campos, dados)
    return redirect(url_for('cliente'))


# ─────────────────────────── PRODUTOS ──────────────────────────────────────

@app.route('/produto', methods=['GET', 'POST'])
def produto():
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        preco = request.form.get('preco', '0').replace(',', '.')
        quantidade = request.form.get('quantidade', '0')
        cor_primaria = request.form.get('cor_primaria', '').strip()
        cor_secundaria = request.form.get('cor_secundaria', '').strip()

        if nome:
            novo_id = db.next_id(SHEET_PRODUTOS)
            db.append_row(SHEET_PRODUTOS, ['id', 'nome', 'preco', 'quantidade', 'cor_primaria', 'cor_secundaria'], {
                'id': novo_id,
                'nome': nome,
                'preco': float(preco) if preco else 0.0,
                'quantidade': int(quantidade) if quantidade else 0,
                'cor_primaria': cor_primaria,
                'cor_secundaria': cor_secundaria,
            })

        return redirect(url_for('produto'))

    produtos = db.normalize_rows(
        db.get_all_rows(SHEET_PRODUTOS), int_fields=['id', 'quantidade'], float_fields=['preco']
    )
    produtos.sort(key=lambda p: p.get('nome', ''))
    return render_template('produto.html', produtos=produtos, active_page='produto')


@app.route('/remover_produto', methods=['POST'])
def remover_produto():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    produto_id = request.form.get('produto_id')
    db.delete_row(SHEET_PRODUTOS, 'id', produto_id)
    return redirect(url_for('produto'))


# ─────────────────────────── CONSULTAR CHAVE ───────────────────────────────

@app.route('/consultar_chave')
def consultar_chave():
    # Página pública — clientes podem acessar sem login

    chave_buscada = request.args.get('chave', '').strip()
    venda = None
    itens = []

    chave_invalida = False
    if chave_buscada:
        chave_limpa = chave_buscada.replace(' ', '').strip()
        if len(chave_limpa) != 44 or not chave_limpa.isdigit():
            chave_invalida = True
        else:
            vendas = db.normalize_rows(db.get_all_rows(SHEET_VENDAS), int_fields=['id'], float_fields=['valor'])
            venda = next((v for v in vendas if chave_limpa == str(v.get('chave_nfce') or '').replace(' ', '')), None)

        if venda:
            venda['chave_nfce'] = str(venda.get('chave_nfce') or '')
            itens = db.normalize_rows(
                db.get_all_rows(SHEET_VENDA_ITENS),
                int_fields=['id', 'venda_id', 'quantidade'],
                float_fields=['preco_unitario'],
            )
            itens = [i for i in itens if i.get('venda_id') == venda.get('id')]

    return render_template(
        'consultar_chave.html',
        chave_buscada=chave_buscada,
        chave_invalida=chave_invalida,
        venda=venda,
        itens=itens,
        active_page='consultar_chave',
        logged_in=session.get('logged_in', False),
    )


# ─────────────────────────── ENTRY POINT ───────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)