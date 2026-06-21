"""
sheets_db.py
─────────────────────────────────────────────────────────────────────────────
Camada de acesso ao Google Sheets, usada como banco de dados do sistema.

Variáveis de ambiente necessárias:
  GOOGLE_CREDENTIALS_JSON  -> conteúdo completo do arquivo JSON da Service
                              Account (cole o JSON inteiro como uma única
                              variável de ambiente, não como arquivo).
  SPREADSHEET_ID           -> ID da planilha (a parte da URL entre
                              /d/ e /edit).

Nenhuma credencial deve ser commitada no código-fonte.
─────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import time
from pathlib import Path

_BASE_DIR = Path(__file__).parent

# Lê o .env diretamente — funciona independente de os.environ ou CWD
try:
    from dotenv import dotenv_values, load_dotenv
    _ENV = dotenv_values(_BASE_DIR / '.env')
    load_dotenv(dotenv_path=_BASE_DIR / '.env', override=True)
except ImportError:
    _ENV = {}


def _cfg(key):
    """Lê variável do ambiente ou diretamente do .env como fallback."""
    return os.environ.get(key) or _ENV.get(key, '')

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gc = None
_spreadsheet = None
_worksheet_cache = {}


# ─────────────────────────── CONEXÃO ───────────────────────────────────────

def _load_credentials():
    # Coleta candidatos a JSON — aceita qualquer variável que contenha um JSON
    raw = _cfg("GOOGLE_CREDENTIALS_JSON")
    if not (raw and raw.strip().startswith('{')):
        # Fallback: talvez o JSON foi colado na variável errada
        alt = _cfg("GOOGLE_APPLICATION_CREDENTIALS_FILE")
        if alt and alt.strip().startswith('{'):
            raw = alt

    if raw and raw.strip().startswith('{'):
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    # Uso local: GOOGLE_APPLICATION_CREDENTIALS_FILE aponta para um arquivo .json
    file_path = _cfg("GOOGLE_APPLICATION_CREDENTIALS_FILE")
    if file_path and not file_path.strip().startswith('{'):
        resolved = _BASE_DIR / file_path
        if not resolved.exists():
            raise RuntimeError(f"Arquivo de credenciais não encontrado em: {resolved}")
        return Credentials.from_service_account_file(str(resolved), scopes=SCOPES)

    raise RuntimeError(
        "Defina GOOGLE_CREDENTIALS_JSON (conteúdo do JSON da service account) "
        "ou GOOGLE_APPLICATION_CREDENTIALS_FILE (caminho para o credentials.json local)."
    )


def get_client():
    global _gc
    if _gc is None:
        creds = _load_credentials()
        _gc = gspread.authorize(creds)
    return _gc


def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        sheet_id = _cfg("SPREADSHEET_ID")
        if not sheet_id:
            raise RuntimeError("SPREADSHEET_ID não encontrado no ambiente nem no .env.")
        _spreadsheet = get_client().open_by_key(sheet_id)
    return _spreadsheet


def get_worksheet(name):
    if name not in _worksheet_cache:
        _worksheet_cache[name] = get_spreadsheet().worksheet(name)
    return _worksheet_cache[name]


def _with_retry(func, *args, tries=3, **kwargs):
    """Pequena resiliência contra erros transitórios / limite de requisições da API."""
    last_exc = None
    for attempt in range(tries):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise last_exc


# ─────────────────────────── LEITURA ───────────────────────────────────────

def get_all_rows(sheet_name):
    """Retorna a aba como lista de dicts, usando a linha 1 como cabeçalho.
    Usa get_all_values para garantir que todos os valores venham como string,
    evitando que o Sheets converta chaves longas e outros campos em número.
    """
    ws = get_worksheet(sheet_name)
    values = _with_retry(ws.get_all_values)
    if not values:
        return []
    header = values[0]
    return [dict(zip(header, row)) for row in values[1:]]


def _get_header_and_values(sheet_name):
    ws = get_worksheet(sheet_name)
    values = _with_retry(ws.get_all_values)
    if not values:
        return [], []
    return values[0], values[1:]


def find_row_number(sheet_name, key_column, key_value):
    """Número da linha (1-indexado, contando o cabeçalho) onde key_column == key_value."""
    header, rows = _get_header_and_values(sheet_name)
    if key_column not in header:
        return None
    col_idx = header.index(key_column)
    for i, row in enumerate(rows, start=2):
        if col_idx < len(row) and str(row[col_idx]).strip() == str(key_value).strip():
            return i
    return None


def next_id(sheet_name, id_column="id"):
    """Próximo ID disponível (maior id atual + 1). Começa em 1 se a aba estiver vazia."""
    rows = get_all_rows(sheet_name)
    max_id = 0
    for r in rows:
        try:
            v = int(float(r.get(id_column) or 0))
            if v > max_id:
                max_id = v
        except (ValueError, TypeError):
            continue
    return max_id + 1


# ─────────────────────────── ESCRITA ───────────────────────────────────────

def append_row(sheet_name, header_order, row_dict):
    """Adiciona uma linha respeitando a ordem de colunas indicada em header_order."""
    ws = get_worksheet(sheet_name)
    row = [row_dict.get(col, "") for col in header_order]
    _with_retry(ws.append_row, row, value_input_option="USER_ENTERED")


def delete_row(sheet_name, key_column, key_value):
    row_num = find_row_number(sheet_name, key_column, key_value)
    if row_num:
        ws = get_worksheet(sheet_name)
        _with_retry(ws.delete_rows, row_num)
        return True
    return False


def update_cell(sheet_name, key_column, key_value, target_column, new_value):
    """Atualiza uma célula de uma linha identificada por key_column == key_value."""
    header, rows = _get_header_and_values(sheet_name)
    if key_column not in header or target_column not in header:
        return False
    key_idx = header.index(key_column)
    target_idx = header.index(target_column) + 1  # gspread usa 1-indexado

    for i, row in enumerate(rows, start=2):
        if key_idx < len(row) and str(row[key_idx]).strip() == str(key_value).strip():
            ws = get_worksheet(sheet_name)
            _with_retry(ws.update_cell, i, target_idx, new_value)
            return True
    return False


# ─────────────────────────── HELPERS DE TIPO ──────────────────────────────

def to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def normalize_rows(rows, int_fields=None, float_fields=None):
    """Converte campos numéricos vindos do Sheets (que podem chegar como str) para int/float."""
    int_fields = int_fields or []
    float_fields = float_fields or []
    for r in rows:
        for f in int_fields:
            r[f] = to_int(r.get(f))
        for f in float_fields:
            r[f] = to_float(r.get(f))
    return rows
