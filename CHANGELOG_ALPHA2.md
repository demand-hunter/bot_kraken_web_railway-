# Changelog alpha2

Baseline preservada conceitualmente: risco, RR, paper mode e gatilhos não foram recalibrados.

Mudanças isoladas desta versão:
1. `TIMEFRAME=5m` deixou de comandar a decisão; `DECISION_TIMEFRAME=15m` é o padrão.
2. Universo é descoberto na Kraken e limitado a 100 pares spot USD/USDT.
3. Radar incremental pontua uma pequena leva a cada ciclo e mantém Top 25 dinâmico.
4. Tendência passa por confirmação 1H + 15M antes de S/R liberar gatilho.
5. Rompimentos de nível são apenas observados em shadow.
6. Log passa a mostrar quase-sinais e gargalo x/5.

Nenhuma ordem real é enviada.
