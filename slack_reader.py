import re
import json
import os
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SLACK_TOKEN     = "xoxb-224351624935-11118613936423-Y6n8kn97tj0b8MQbPCTXDuxO"
CHANNEL_NAME    = "nuclea-slc-reconciliation"
CHANNEL_PENDING = "nuclea-slc-pending"
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON   = os.path.join(SCRIPT_DIR, "dados.json")
OUTPUT_HTML   = os.path.join(SCRIPT_DIR, "dashboard.html")

def limpar(texto):
    texto = re.sub(r"\*([^*]+)\*", r"\1", texto)
    texto = re.sub(r":[a-z_]+:", "", texto)
    texto = re.sub(r"<[^>]+>", "", texto)
    return texto

def extrair_valor(texto):
    match = re.search(r"R\$\s*([\d\.]+,\d{2})", texto)
    if match:
        return float(match.group(1).replace(".", "").replace(",", "."))
    return 0.0

def extrair_instrucoes(texto):
    match = re.search(r"(\d+)\s+instru", texto)
    return int(match.group(1)) if match else 0

def buscar_canais(client, nomes):
    """Retorna um dict {nome: id} para todos os canais pedidos, em uma única varredura."""
    resultado = {n: None for n in nomes}
    encontrados = 0
    try:
        for page in client.conversations_list(types="public_channel,private_channel"):
            for ch in page["channels"]:
                if ch["name"] in resultado and resultado[ch["name"]] is None:
                    resultado[ch["name"]] = ch["id"]
                    encontrados += 1
                if encontrados == len(nomes):
                    return resultado
    except SlackApiError as e:
        print(f"Erro ao listar canais: {e.response['error']}")
    return resultado

def parsear_pendentes(client, channel_id):
    """Busca mensagens das últimas 24h do canal nuclea-slc-pending e extrai as instruções pendentes."""
    from datetime import timezone, timedelta

    oldest_ts = str((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())

    pendentes = []
    try:
        resp = client.conversations_history(channel=channel_id, oldest=oldest_ts, limit=200)
    except SlackApiError as e:
        print(f"Erro ao buscar pendentes: {e.response['error']}")
        return pendentes

    for msg in resp.get("messages", []):
        # As mensagens vêm como attachments de webhook
        for att in msg.get("attachments", []):
            texto = att.get("text", "") + "\n" + att.get("fallback", "") + "\n" + att.get("pretext", "")
            texto = limpar(texto)
            if "pending" not in texto.lower() and "instrução filha" not in texto.lower():
                continue

            filha  = re.search(r"instru[çc][aã]o filha.*?#?(\d+)", texto, re.IGNORECASE)
            pai    = re.search(r"instru[çc][aã]o pai.*?#?(\d+)",   texto, re.IGNORECASE)
            shop   = re.search(r"shop[:\s]+(\d+)",                  texto, re.IGNORECASE)
            payout = re.search(r"payout[:\s]+(\d+)",                texto, re.IGNORECASE)

            if filha:
                pendentes.append({
                    "filha":  filha.group(1),
                    "pai":    pai.group(1)    if pai    else "—",
                    "shop":   shop.group(1)   if shop   else "—",
                    "payout": payout.group(1) if payout else "—",
                    "hora":   datetime.fromtimestamp(float(msg["ts"])).strftime("%H:%M"),
                })

        # Tenta também no corpo da mensagem diretamente
        texto = limpar(msg.get("text", ""))
        if "pending" in texto.lower() or "instrução filha" in texto.lower():
            filha  = re.search(r"instru[çc][aã]o filha.*?#?(\d+)", texto, re.IGNORECASE)
            pai    = re.search(r"instru[çc][aã]o pai.*?#?(\d+)",   texto, re.IGNORECASE)
            shop   = re.search(r"shop[:\s]+(\d+)",                  texto, re.IGNORECASE)
            payout = re.search(r"payout[:\s]+(\d+)",                texto, re.IGNORECASE)
            if filha:
                pendentes.append({
                    "filha":  filha.group(1),
                    "pai":    pai.group(1)    if pai    else "—",
                    "shop":   shop.group(1)   if shop   else "—",
                    "payout": payout.group(1) if payout else "—",
                    "hora":   datetime.fromtimestamp(float(msg["ts"])).strftime("%H:%M"),
                })

    print(f"{len(pendentes)} instrução(ões) pendente(s) encontrada(s) hoje.")
    return pendentes

def parsear_mensagem(texto_raw):
    texto = limpar(texto_raw)
    resultado = {
        "data_ref": None,
        "total_valor": 0.0,
        "liquidados":     {"qtd": 0, "valor": 0.0},
        "devolvidos":     {"qtd": 0, "valor": 0.0},
        "atrasados":      {"qtd": 0, "valor": 0.0},
        "rejeitados":     {"qtd": 0, "valor": 0.0},
        "falhas":         {"qtd": 0, "valor": 0.0},
        "banco_pendente": {"qtd": 0, "valor": 0.0},
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    m = re.search(r"Data ref[:\s]*([\d\-/]+)\s*\|\s*Total:\s*(R\$[\s\d\.,]+)", texto, re.IGNORECASE)
    if m:
        resultado["data_ref"]    = m.group(1).strip()
        resultado["total_valor"] = extrair_valor(m.group(2))

    for linha in texto.splitlines():
        l = linha.lower()
        if "liquidação atrasada" in l or "liquidacao atrasada" in l:
            resultado["atrasados"]["qtd"]   = extrair_instrucoes(linha)
            resultado["atrasados"]["valor"] = extrair_valor(linha)
        elif "rejeitad" in l and ("núclea" in l or "nuclea" in l):
            resultado["rejeitados"]["qtd"]   = extrair_instrucoes(linha)
            resultado["rejeitados"]["valor"] = extrair_valor(linha)
        elif "falha técnica" in l or "falha tecnica" in l:
            resultado["falhas"]["qtd"]   = extrair_instrucoes(linha)
            resultado["falhas"]["valor"] = extrair_valor(linha)
        elif "liquidado" in l and "atrasad" not in l:
            resultado["liquidados"]["qtd"]   = extrair_instrucoes(linha)
            resultado["liquidados"]["valor"] = extrair_valor(linha)
        elif "devolvido" in l or "devolução" in l:
            resultado["devolvidos"]["qtd"]   = extrair_instrucoes(linha)
            resultado["devolvidos"]["valor"] = extrair_valor(linha)
        elif "aguardando confirmação" in l or "aguardando confirmacao" in l:
            m_itens = re.search(r"([\d\.]+)\s+iten", linha)
            resultado["banco_pendente"]["qtd"]   = int(m_itens.group(1).replace(".", "")) if m_itens else 0
            resultado["banco_pendente"]["valor"] = extrair_valor(linha)

    if resultado["liquidados"]["valor"] == 0.0 and resultado["total_valor"] > 0:
        total_pend = (
            resultado["atrasados"]["valor"] +
            resultado["rejeitados"]["valor"] +
            resultado["falhas"]["valor"] +
            resultado["devolvidos"]["valor"] +
            resultado["banco_pendente"]["valor"]
        )
        calc = round(resultado["total_valor"] - total_pend, 2)
        resultado["liquidados"]["valor"] = calc if calc > 0 else 0.0

    return resultado

def atualizar_html(dados, pendentes):
    """Injeta os dados diretamente no dashboard.html para funcionar sem servidor."""
    with open(OUTPUT_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    dados_js     = f"const DADOS_INJETADOS = {json.dumps(dados, ensure_ascii=False)};"
    pendentes_js = f"const PENDENTES_INJETADOS = {json.dumps(pendentes, ensure_ascii=False)};"

    novas = []
    for linha in html.splitlines():
        s = linha.strip()
        if s.startswith("const DADOS_INJETADOS"):
            novas.append(dados_js)
        elif s.startswith("const PENDENTES_INJETADOS"):
            novas.append(pendentes_js)
        else:
            novas.append(linha)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(novas))
    print(f"Dashboard atualizado: {OUTPUT_HTML}")

def main():
    client = WebClient(token=SLACK_TOKEN)

    # Busca os dois canais em uma única chamada à API
    canais = buscar_canais(client, [CHANNEL_NAME, CHANNEL_PENDING])
    channel_id = canais.get(CHANNEL_NAME)
    pending_id = canais.get(CHANNEL_PENDING)

    # ── Canal de reconciliação ──────────────────────────────────────────────
    if not channel_id:
        print(f"Canal '{CHANNEL_NAME}' não encontrado.")
        return
    print(f"Canal reconciliação: {channel_id}")

    try:
        resp = client.conversations_history(channel=channel_id, limit=10)
    except SlackApiError as e:
        print(f"Erro ao buscar mensagens: {e.response['error']}")
        return

    texto_encontrado = None
    for msg in resp["messages"]:
        for att in msg.get("attachments", []):
            att_text = att.get("text","") + "\n" + att.get("fallback","") + "\n" + att.get("pretext","")
            if "data ref" in att_text.lower() or "instruc" in att_text.lower() or "instruç" in att_text.lower():
                texto_encontrado = att_text
                break
        if texto_encontrado:
            break
        texto = msg.get("text", "")
        if "data ref" in texto.lower() or "instruc" in texto.lower():
            texto_encontrado = texto
            break

    if not texto_encontrado:
        print("Nenhuma mensagem de reconciliação encontrada.")
        return

    print("Mensagem de reconciliação encontrada! Parseando...")
    dados = parsear_mensagem(texto_encontrado)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    print(json.dumps(dados, ensure_ascii=False, indent=2))

    # ── Canal de pendentes ──────────────────────────────────────────────────
    pendentes = []
    if pending_id:
        print(f"Canal pendentes: {pending_id}")
        pendentes = parsear_pendentes(client, pending_id)
    else:
        print(f"Canal '{CHANNEL_PENDING}' não encontrado — seguindo sem pendentes.")

    atualizar_html(dados, pendentes)
    print("\nPronto! Abra o dashboard.html no navegador.")

if __name__ == "__main__":
    main()
