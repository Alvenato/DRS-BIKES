import os
import random
import datetime
from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'chave_secreta_para_sessoes_do_sistema_pdv'

DATABASE = 'sistema_vendas.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL, email TEXT, telefone TEXT, whatsapp TEXT,
            cep TEXT, logradouro TEXT, numero TEXT, complemento TEXT,
            bairro TEXT, city TEXT, uf TEXT, data_nasc TEXT, genero TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            nome TEXT NOT NULL, 
            preco REAL NOT NULL,
            quantidade INTEGER DEFAULT 0,
            cor_primaria TEXT,
            cor_secundaria TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            cliente TEXT NOT NULL, 
            valor REAL NOT NULL, 
            data TEXT NOT NULL, 
            chave_nfce TEXT NOT NULL,
            forma_pagamento TEXT DEFAULT 'Pix'
        )
    ''')
    
    try:
        cursor.execute("ALTER TABLE vendas ADD COLUMN forma_pagamento TEXT DEFAULT 'Pix'")
    except sqlite3.OperationalError:
        pass  
        
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS itens_venda (
            id INTEGER PRIMARY KEY AUTOINCREMENT, venda_id INTEGER, produto_nome TEXT, quantidade INTEGER, preco_unitario REAL, FOREIGN KEY(venda_id) REFERENCES vendas(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            primeiro_acesso INTEGER DEFAULT 1
        )
    ''')
    
    cursor.execute("SELECT * FROM usuarios WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO usuarios (username, password, primeiro_acesso) VALUES ('admin', 'admin', 1)")
        
    conn.commit()
    conn.close()

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE username = ?", (username,))
        usuario = cursor.fetchone()
        conn.close()
        
        if usuario:
            senha_correta = False
            if usuario['primeiro_acesso'] == 1 and password == usuario['password']:
                senha_correta = True
            elif check_password_hash(usuario['password'], password):
                senha_correta = True
                
            if senha_correta:
                session['logged_in'] = True
                session['username'] = usuario['username']
                
                if usuario['primeiro_acesso'] == 1:
                    session['mudar_senha_obrigatorio'] = True
                    return redirect(url_for('alterar_senha_obrigatoria'))
                    
                return redirect(url_for('admin'))
        
    return render_template('login.html', error="Usuário ou senha inválidos.")

@app.route('/alterar-senha', methods=['GET', 'POST'])
def alterar_senha_obrigatoria():
    if not session.get('logged_in') or not session.get('mudar_senha_obrigatorio'):
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirmar_senha = request.form.get('confirmar_senha')
        
        if not nova_senha or nova_senha == 'admin':
            return render_template('alterar_senha.html', error="A nova senha não pode ser 'admin'.")
            
        if nova_senha != confirmar_senha:
            return render_template('alterar_senha.html', error="As senhas informadas não coincidem.")
            
        senha_criptografada = generate_password_hash(nova_senha)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE usuarios 
            SET password = ?, primeiro_acesso = 0 
            WHERE username = ?
        ''', (senha_criptografada, session['username']))
        conn.commit()
        conn.close()
        
        session.pop('mudar_senha_obrigatorio', None)
        return redirect(url_for('admin'))
        
    return render_template('alterar_senha.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if session.get('mudar_senha_obrigatorio'):
        return redirect(url_for('alterar_senha_obrigatoria'))
        
    dia = request.args.get('dia', '')
    mes = request.args.get('mes', '')
    ano = request.args.get('ano', '')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT id, cliente, valor, data, chave_nfce, forma_pagamento FROM vendas WHERE 1=1"
    params = []

    if dia:
        dia_formatado = dia.zfill(2)
        query += " AND substr(data, 1, 2) = ?"
        params.append(dia_formatado)
        
    if mes:
        query += " AND substr(data, 4, 2) = ?"
        params.append(mes)
        
    if ano:
        query += " AND substr(data, 7, 4) = ?"
        params.append(ano)

    query += " ORDER BY id DESC"
    
    cursor.execute(query, tuple(params))
    vendas_raw = cursor.fetchall()
    conn.close()
    
    vendas = []
    for row in vendas_raw:
        vendas.append([row['id'], row['cliente'], row['valor'], row['data'], row['chave_nfce'], row['forma_pagamento']])
    
    total_vendas = len(vendas)
    faturamento_total = sum(float(v[2]) for v in vendas) if total_vendas > 0 else 0.0
    ticket_medio = (faturamento_total / total_vendas) if total_vendas > 0 else 0.0
    
    # --- Cálculo de Faturamento por Forma de Pagamento ---
    faturamento_dinheiro = 0.0
    faturamento_pix = 0.0
    faturamento_credito = 0.0
    faturamento_debito = 0.0
    
    for v in vendas:
        valor_venda = float(v[2])
        forma_pag = str(v[5]).strip().lower() 
        
        if 'dinheiro' in forma_pag:
            faturamento_dinheiro += valor_venda
        elif 'pix' in forma_pag:
            faturamento_pix += valor_venda
        elif 'cred' in forma_pag or 'crédito' in forma_pag:
            faturamento_credito += valor_venda
        elif 'deb' in forma_pag or 'débito' in forma_pag:
            faturamento_debito += valor_venda
    
    return render_template(
        'admin.html', 
        vendas=vendas, 
        active_page='admin',
        total_vendas=total_vendas,
        faturamento_total=faturamento_total,
        ticket_medio=ticket_medio,
        faturamento_dinheiro=faturamento_dinheiro,
        faturamento_pix=faturamento_pix,
        faturamento_credito=faturamento_credito,
        faturamento_debito=faturamento_debito
    )

@app.route('/cliente')
def cliente():
    if session.get('mudar_senha_obrigatorio'): 
        return redirect(url_for('alterar_senha_obrigatoria'))
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, nome, email, telefone, whatsapp, cep, logradouro, 
               numero, complemento, bairro, city AS cidade, uf, data_nasc, genero 
        FROM clientes 
        ORDER BY nome ASC
    ''')
    clientes = cursor.fetchall()
    conn.close()
    
    return render_template('cliente.html', active_page='cliente', clientes=clientes)

@app.route('/add_cliente', methods=['POST'])
def add_cliente():
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO clientes (nome, email, telefone, whatsapp, cep, logradouro, numero, complemento, bairro, city, uf, data_nasc, genero)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form.get('nome'), request.form.get('email'), request.form.get('telefone'),
            request.form.get('whatsapp'), request.form.get('cep'), request.form.get('logradouro'),
            request.form.get('numero'), request.form.get('complemento'), request.form.get('bairro'),
            request.form.get('cidade'), request.form.get('uf'), request.form.get('data_nasc'),
            request.form.get('genero')
        ))
        conn.commit()
        conn.close()
    return redirect(url_for('cliente'))

@app.route('/produto')
def produto():
    if session.get('mudar_senha_obrigatorio'): 
        return redirect(url_for('alterar_senha_obrigatoria'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, preco, quantidade, cor_primaria, cor_secundaria FROM produtos ORDER BY nome ASC")
    produtos = cursor.fetchall()
    conn.close()
    return render_template('produto.html', produtos=produtos, active_page='produto')

@app.route('/add_produto', methods=['POST'])
def add_produto():
    if request.method == 'POST':
        nome = request.form.get('nome')
        preco = request.form.get('preco')
        quantidade = request.form.get('quantidade')
        cor_primaria = request.form.get('cor_primaria')
        cor_secundaria = request.form.get('cor_secundaria')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO produtos (nome, preco, quantidade, cor_primaria, cor_secundaria) 
            VALUES (?, ?, ?, ?, ?)
        ''', (nome, preco, quantidade, cor_primaria, cor_secundaria))
        conn.commit()
        conn.close()
    return redirect(url_for('produto'))

@app.route('/remover_produto', methods=['POST'])
def remover_produto():
    if request.method == 'POST':
        produto_id = request.form.get('produto_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
        conn.commit()
        conn.close()
        
    return redirect(url_for('produto'))

@app.route('/caixa')
def caixa():
    if session.get('mudar_senha_obrigatorio'): 
        return redirect(url_for('alterar_senha_obrigatoria'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nome FROM clientes ORDER BY nome ASC")
    clientes = cursor.fetchall()
    
    cursor.execute("SELECT id, nome, preco, quantidade, cor_primaria, cor_secundaria FROM produtos ORDER BY nome ASC")
    produtos = cursor.fetchall()
    conn.close()
    
    error = session.pop('caixa_error', None)
    return render_template('caixa.html', clientes=clientes, produtos=produtos, active_page='caixa', error=error)

@app.route('/registrar_venda', methods=['POST'])
def registrar_venda():
    if request.method == 'POST':
        nome_cliente = request.form.get('nome_cliente', 'CONSUMIDOR NAO IDENTIFICADO')
        forma_pagamento = request.form.get('forma_pagamento', 'Pix')
        
        lista_produtos = request.form.getlist('produtos[]')
        lista_qtd = request.form.getlist('qtd[]')
        lista_preco = request.form.getlist('preco[]')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for i in range(len(lista_produtos)):
            if lista_produtos[i]:
                nome_prod = lista_produtos[i]
                qtd_solicitada = int(lista_qtd[i])
                
                cursor.execute("SELECT quantidade FROM produtos WHERE nome = ?", (nome_prod,))
                produto_banco = cursor.fetchone()
                
                if not produto_banco:
                    session['caixa_error'] = f"O produto '{nome_prod}' não foi localizado no estoque."
                    conn.close()
                    return redirect(url_for('caixa'))
                
                estoque_atual = produto_banco['quantidade']
                if qtd_solicitada > estoque_atual:
                    session['caixa_error'] = f"Operação cancelada! '{nome_prod}' possui apenas {estoque_atual} un. em estoque (você tentou vender {qtd_solicitada})."
                    conn.close()
                    return redirect(url_for('caixa'))
        
        chave_nfce = "".join([str(random.randint(0, 9)) for _ in range(44)])
        data_atual = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        valor_total = 0.0
        itens_para_salvar = []
        
        for i in range(len(lista_produtos)):
            if lista_produtos[i]:
                nome_prod = lista_produtos[i]
                qtd = int(lista_qtd[i])
                preco_un = float(lista_preco[i])
                
                valor_total += (qtd * preco_un)
                itens_para_salvar.append((nome_prod, qtd, preco_un))
                
                cursor.execute("UPDATE produtos SET quantidade = quantity - ? WHERE nome = ?", (qtd, nome_prod)) if 'quantity' in str(produto_banco.keys()) else cursor.execute("UPDATE produtos SET quantidade = quantidade - ? WHERE nome = ?", (qtd, nome_prod))
        
        cursor.execute('''
            INSERT INTO vendas (cliente, valor, data, chave_nfce, forma_pagamento) 
            VALUES (?, ?, ?, ?, ?)
        ''', (nome_cliente, valor_total, data_atual, chave_nfce, forma_pagamento))
        
        venda_id = cursor.lastrowid
        
        for item in itens_para_salvar:
            cursor.execute('INSERT INTO itens_venda (venda_id, produto_nome, quantidade, preco_unitario) VALUES (?, ?, ?, ?)', (venda_id, item[0], item[1], item[2]))
            
        conn.commit()
        conn.close()
        return redirect(url_for('admin'))

@app.route('/imprimir_nf/<int:venda_id>')
def imprimir_nf(venda_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, cliente, valor, data, chave_nfce, forma_pagamento FROM vendas WHERE id = ?", (venda_id,))
    venda = cursor.fetchone()
    
    if not venda: 
        conn.close()
        return "Venda não localizada.", 404

    cursor.execute("SELECT whatsapp FROM clientes WHERE nome = ?", (venda['cliente'],))
    cliente_banco = cursor.fetchone()
    
    whatsapp_cliente = cliente_banco['whatsapp'] if cliente_banco else ""
    
    cursor.execute("SELECT id, venda_id, produto_nome, quantidade, preco_unitario FROM itens_venda WHERE venda_id = ?", (venda_id,))
    itens_raw = cursor.fetchall()
    conn.close()
    
    venda_dados = [
        venda['id'],              # [0]
        venda['cliente'],          # [1]
        venda['valor'],            # [2]
        venda['data'],             # [3]
        venda['chave_nfce'],       # [4]
        venda['forma_pagamento'],  # [5]
        whatsapp_cliente           # [6]
    ]
    
    itens = [[idx + 1, item['venda_id'], item['produto_nome'], item['quantidade'], item['preco_unitario']] for idx, item in enumerate(itens_raw)]
    return render_template('modelo_nf.html', venda=venda_dados, itens=itens, modo_publico=False)

@app.route('/cupom/<string:chave_nfce>')
def exibir_cupom_publico(chave_nfce):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, cliente, valor, data, chave_nfce, forma_pagamento FROM vendas WHERE chave_nfce = ?", (chave_nfce,))
    venda = cursor.fetchone()
    
    if not venda:
        conn.close()
        return "Cupom Fiscal não localizado no sistema.", 404
        
    cursor.execute("SELECT id, venda_id, produto_nome, quantidade, preco_unitario FROM itens_venda WHERE venda_id = ?", (venda['id'],))
    itens_raw = cursor.fetchall()
    conn.close()
    
    venda_dados = [
        venda['id'],
        venda['cliente'],
        venda['valor'],
        venda['data'],
        venda['chave_nfce'],
        venda['forma_pagamento'],
        ""  
    ]
    
    itens = [[idx + 1, item['venda_id'], item['produto_nome'], item['quantidade'], item['preco_unitario']] for idx, item in enumerate(itens_raw)]
    return render_template('modelo_nf.html', venda=venda_dados, itens=itens, modo_publico=True)

if __name__ == '__main__':
    init_db()
    porta = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=porta, debug=True)