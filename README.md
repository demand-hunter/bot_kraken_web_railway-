# Trading Turbo 4.0 — Kraken alpha2 (Paper)

Esta é uma evolução **cirúrgica** do bot Kraken que serviu de baseline.

## O que mudou nesta alpha2
- decisão oficial em **15 minutos** (`DECISION_TIMEFRAME=15m`);
- radar leve de até **100 pares** disponíveis na Kraken;
- **Top 25 dinâmico**: pares entram e saem conforme proximidade estrutural, com pequena histerese para evitar troca nervosa;
- gargalo com **Tendência Confirmada (1H + 15M alinhados)** antes dos gatilhos de S/R;
- suporte/resistência continuam sendo a localização estrutural dos gatilhos;
- eventos de rompimento de níveis são registrados em **shadow** (observação; não interferem na entrada nesta versão);
- telemetria de quase-sinais (`gate x/5`).

## O que NÃO mudou
- PAPER trading: nenhuma ordem real;
- risco por operação: 0,5% por padrão;
- RR: 1,5 por padrão;
- cálculo de stop/target do baseline;
- gatilhos de `rejection` e `breakout_retest` foram preservados, agora avaliados em 15M;
- uma posição global por vez.

## Variáveis principais
- `EXCHANGE_ID=kraken`
- `DECISION_TIMEFRAME=15m`
- `HIGHER_TIMEFRAME=1h`
- `RADAR_SIZE=100`
- `FOCUS_SIZE=25`
- `RADAR_BATCH=5` (quantos pares do radar são atualizados por rodada)
- `RADAR_REFRESH_SECONDS=30`
- `FOCUS_STICKINESS=2.0`
- `RISK_PER_TRADE=0.005`
- `RR=1.5`
- `AUTOSTART=true`

A Kraken pode não ter 100 mercados spot distintos com a cotação preferida no momento. O bot descobre os mercados ativos em runtime e usa até 100 pares USD/USDT, priorizando USDT.

## Railway
O `railway.json` continua compatível. Suba estes arquivos na raiz do repositório conectado ao Railway.

## Segurança
Somente PAPER TRADING. Resultados históricos ou simulados não garantem lucro futuro.
