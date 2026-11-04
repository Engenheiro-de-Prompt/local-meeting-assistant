# Proteção de recursos e fallback de reuniões — escopo proposto

Documento histórico de projeto, anterior à implementação. As referências de linha e os resultados abaixo registram a auditoria inicial, não o estado atual do código. A política implementada e seus limites estão em `resource-safety-operation.md`; os critérios de preservação estão em `audio-preservation-scope.md`. Calibração e ensaios de reunião sob carga continuam pendentes.

## Objetivo e limites

Priorizar chamada, apresentação e captura de áudio acima de qualquer inferência. Degradar o assistente preventivamente, sem encerrar aplicações alheias. Não prometer ausência absoluta de travamentos: processos externos, drivers e sistema operacional estão fora de seu controle. Validar uma margem operacional com ensaios no hardware real, fora de reuniões.

## Evidências locais

- `live_meeting_app.py:330–377`: amostra RAM aproximada e load average; no macOS não mede pressão real de memória, CPU instantânea ou GPU. `829–845`: monitor coleta/agrega, não controla processamento.
- `live_meeting_app.py:1488–1529`: fila LLM coalescida e worker independente. `1468–1486`: Whisper no processo do servidor, sem limite explícito de threads nessa construção.
- `live_meeting_app.py:1538–1599`: chamadas LLM continuam até retornar; validar estado depois não equivale a liberar recursos durante inferência.
- `live_meeting_app.py:1728–1734`: modo off avança cursor sem transcrever. Não reutilizar esse modo como pausa temporária de fallback.
- `live_meeting_app.py:1801–1814`, `1332–1335`, `1401–1417`: erro de processamento pode deixar recorder vivo, enquanto parada depende de estado recording. Corrigir supervisão e bloqueio de nova sessão pelo processo real antes do fallback.
- `live_meeting_app.py:1718–1724`, `1443–1466`: varreduras de chunks e regravação integral de artefatos crescem com a sessão. Separar telemetria de persistência pesada.
- `live_meeting_app.py:1774–1791`: encerramento pode forçar análise ao vivo e análise final. Não iniciar essa carga sob pressão.
- `web/index.html:54`: intervalo LLM limitado na interface a 60/180/300 s. Backend já admite valores inteiros entre 60 e 600 s (`1007–1009`); isso não é o intervalo Whisper.

## Arquitetura proposta

Supervisor no backend, independente de navegador. Separar captura, transcrição e análise em estados próprios. Modelo de configuração guarda intenção do usuário; estado efetivo guarda limitações temporárias e motivos. Usar timestamps monotônicos, amostras atuais e janela móvel, preservando picos somente como histórico.

Whisper em subprocesso controlável, checkpoint atômico por chunk e orçamento explícito de threads. Limitar concorrência de inferência pesada inicialmente a uma tarefa; calibrar qualquer paralelismo posteriormente. Cancelamento Ollama exige verificar que trabalho servidor terminou: descartar resposta ou fechar conexão não basta como prova. Não matar Ollama compartilhado; se o endpoint dedicado não for realmente exclusivo, recusar ações destrutivas e sinalizar limitação.

Supervisor deve permanecer responsivo quando modelos travam. Falha/staleness de telemetria impede novas inferências e preserva captura supervisionada. Avaliar um watchdog mínimo separado após medir o custo e os modos de falha do supervisor.

## Sinais e política

Medir CPU total e atribuída ao assistente/processos controlados; pressão de memória, swap em janela, recursos térmicos quando disponíveis sem privilégios; atraso e duração de inferência; saúde/progresso de captura; espaço de disco e reserva para finalizar. Métricas indisponíveis são desconhecidas, não zero. Saúde da chamada não é demonstrada apenas por CPU baixa.

Limiares numéricos de CPU, RAM e GPU ficam pendentes de calibração. Proposta inicial para teste: amostrar a cada 2–5 s; pressão moderada sustentada por 10–15 s degrada; risco grave pula diretamente a só gravação. Recuperação requer 60–120 s saudáveis, um estágio por vez, com cooldown. Estes tempos são hipóteses, não parâmetros comprovados.

Estágios:
1. Normal, sob orçamento; sem concorrência pesada por padrão.
2. Reduzir cards conforme prioridade definida pelo usuário, mantendo só card prioritário quando possível; ampliar intervalo LLM.
3. Suspender todo LLM, incluindo compactação, retries e análise final automática; preservar resultados visíveis marcados como desatualizados.
4. Adiar Whisper e executá-lo em janelas de trabalho limitadas com teto de CPU/threads; ampliar intervalo não autoriza um lote enorme sem orçamento.
5. Só gravação: interromper inferência controlada, liberar modelos quando verificável e manter áudio/checkpoints.

Carga grave pode saltar estágios. Carga causada por outros programas também deve reduzir o assistente; identificação do culpado serve para informar, não para encerrar software externo. Falta de disco exige aviso e encerramento seguro quando necessário; só gravação não resolve disco cheio.

Pausar não avança cursor de transcrição. Backlog permanece em disco e é retomado explicitamente ou sob orçamento saudável, nunca despejado de uma vez. Encerrar gravação não deve exigir concluir IA; processamento final pode ficar pendente.

## Interface

Campos numéricos distintos, com unidade s/min: intervalo de atualização LLM e janela/intervalo de processamento Whisper. Captura conserva segmentos curtos e estáveis: não reiniciar FFmpeg para espaçar IA. Validar limites no servidor e navegador, aceitar somente valores finitos válidos e persistir preferências. Mostrar valor solicitado e efetivo, motivo, estágio, último resultado, backlog, saúde de captura e botão apenas gravar. Analisar agora não pode contornar proteção.

Definir prioridade dos cards pelo usuário. Na ausência de escolha, suspender todos é preferível a escolher silenciosamente um card supostamente importante.

Monitor nativo leve é uma segunda entrega opcional: item na barra de menus/janela compacta, sem Chromium/Electron, consumindo a mesma telemetria local. Não duplicar controlador. O backend deve funcionar com dashboard fechado. Aplicativo macOS Monitor de Atividade já é nativo; o dashboard atual do assistente é que usa navegador.

## Entregas e critérios de aceitação

A. Integridade: erro Whisper/persistência deixa captura supervisionada e parável; segundo start recusado enquanto recorder vivo; pausa/retomada sem perder ou duplicar chunks.
B. Supervisor: transições determinísticas com relógio e métricas injetáveis; pico isolado não oscila; emergência pula etapas; recuperação lenta; telemetria ausente degrada; comandos manuais não burlam limites.
C. Isolamento: worker Whisper pode ser encerrado dentro de deadline definido e medido; teste real comprova liberação de trabalho LLM, sem afetar instâncias de terceiros. Se impossível, declarar proteção parcial, não sucesso.
D. UI: campo numérico em captura e importação; validação API/UI; preferências restauradas; estado efetivo e motivo visíveis; operação protegida com navegador fechado.
E. Validação fora de reunião: simular pressão antes de carga real; ensaios limitados de CPU/RAM, chamada e compartilhamento com autorização; medir captura vs tempo de parede, gaps, atraso de áudio/vídeo, consumo do monitor e recuperação. Nunca gerar swap ou risco deliberado de travamento para provar proteção.
F. Segunda entrega: monitor nativo opcional; abrir sem navegador, exibir mesmos estados e comprovar orçamento leve medido.

## Verificação realizada nesta análise

`python -m unittest discover -s tests -p 'test_resource_metrics.py' -v`: 2 testes aprovados.
`python -m unittest discover -s tests -p 'test_analysis_scheduling.py' -v`: 10 testes aprovados.

São testes de métricas/agendamento com simulações, não validação real de capacidade nem garantia de reunião sem lentidão. Implementação, calibração, ensaio real e monitor nativo continuam pendentes.
