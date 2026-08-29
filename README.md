# Bot Kraken Web — Paper Trading

Projeto único com:
- bot de paper trading;
- dados públicos da Kraken;
- estratégia de suporte/resistência + tendência;
- interface web;
- saldo inicial fictício de R$ 1.000;
- sem ordens reais e sem chave de API.

## Arquivos
- `app.py` — servidor web e interface.
- `bot_engine.py` — estratégia e motor do bot.
- `requirements.txt` — dependências.
- `railway.json` — comando de inicialização no Railway.

## Railway
Suba estes arquivos na raiz de um repositório GitHub e conecte o repositório ao Railway.

O Railway executará:
`gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 app:app`

Depois gere um domínio público no Railway para abrir a interface.

## Variáveis opcionais
- `EXCHANGE_ID=kraken`
- `SYMBOL=BTC/USDT`
- `TIMEFRAME=5m`
- `HIGHER_TIMEFRAME=1h`
- `STARTING_BALANCE=1000`
- `RISK_PER_TRADE=0.005`
- `RR=1.5`
- `POLL_SECONDS=30`
- `AUTOSTART=true`

## Teste local
1. `pip install -r requirements.txt`
2. `python app.py`
3. Abra `http://127.0.0.1:8080`

## Importante
Esta versão é somente PAPER TRADING. Não envia ordens reais.
Resultados de simulação não garantem resultados futuros.
