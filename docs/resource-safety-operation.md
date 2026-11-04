# Proteção de recursos — operação e validação

Os limiares são padrões preventivos iniciais, não certificação de capacidade nem garantia contra lentidão de outros aplicativos. Esta documentação descreve a implementação, não o estado de implantação de uma máquina específica.

## Operação

A proteção roda no backend e independe de navegador ou monitor nativo. O dashboard agora aceita números inteiros:

- Análise LLM: 60–600 segundos.
- Transcrição: 10–3600 segundos, padrão 45. Cadência de início a início, incluindo o tempo gasto no processamento.

Captura permanece em segmentos curtos; espaçar IA não reinicia a gravação. O painel mostra solicitação versus configuração efetiva, motivo, atraso, captura e estado de liberação do LLM. O botão **Somente gravar** pausa IA sem avançar o cursor de áudio pendente. A API permite retirar a pausa manual, mas pressão, falha ou telemetria insuficiente continuam prevalecendo.

Fallback atual: suspender LLM primeiro (nenhum card é escolhido como prioritário sem configuração do usuário), ampliar cadência Whisper, adiar Whisper e manter somente gravação. Risco crítico salta diretamente para somente gravação. Recuperação automática exige 60 segundos saudáveis por estágio. Falhas persistentes e liberação LLM não comprovada ficam bloqueadas por segurança.

No macOS, modelos Whisper são executados em subprocesso descartável, baixa prioridade, duas threads e orçamento de 120 segundos por trecho. Cancelamento termina o filho e preserva áudio/cursor. Só uma inferência pesada é admitida por vez. Esses limites não são quotas rígidas de CPU do sistema operacional.

## Limiares iniciais

- CPU total >=75% por 10 segundos degrada; >=95% é emergência imediata.
- Reserva RAM de alerta: maior entre 1536 MiB e 15% da RAM instalada; emergência: maior entre 512 MiB e 8%; recuperação exige maior entre 2048 MiB e 20%.
- Pressão de memória e crescimento de swap podem antecipar a redução. Recuperação exige CPU <60%, pressão normal e folga de disco/swap.
- Amostra ausente ou antiga (>15 s) suspende inferências. Térmica indisponível permanece desconhecida.
- Captura/importação possuem verificações de disco. Importação decodificada tem teto explícito de 4 GiB; exceder recusa o processamento, sem aceitar transcrição truncada. A decodificação FFmpeg é supervisionada para pressão crítica, reserva de disco, cancelamento e tempo limite, com uma thread.

Não são valores medidos como limite de desempenho desta máquina: são uma política preventiva inicial. Não executar testes de exaustão durante reuniões.

## Monitor nativo

`native-monitor/build/Meeting Resource Monitor.app` é um aplicativo Swift/AppKit na barra de menus. Não abre navegador ao iniciar, não instala login item e não duplica o controlador. Requer backend ativo.

- Compilar: `sh native-monitor/build.sh`
- Abrir: `sh native-monitor/run.sh`
- Verificar protocolo sem abrir interface: `sh native-monitor/run.sh --self-test`

A ação de somente gravar usa POST com Origin exato e GET de leitura posterior. O cliente não exibe transcrição/notas/análises no menu. Atualmente consulta `/api/status`, cujo JSON também contém esses dados; descarta-os após leitura. Um endpoint exclusivo de telemetria pode reduzir ainda mais o custo em sessões longas.

## Cancelamento LLM e limites

As requisições LLM interrompem o próprio socket em pausa, pressão, shutdown ou deadline global de 120 segundos (incluindo compactação/retries). Payloads usam duas threads e CPU explícita (`num_gpu: 0`), preservando GPU para apresentação. Testes com servidor HTTP local lento comprovam interrupção do socket, não o comportamento de um servidor de terceiros.

A instância Ollama não é encerrada ou descarregada à força: uma porta local, mesmo dedicada por convenção, não prova exclusividade de uso. Desconexão do cliente não é prova de liberação pelo servidor. O painel diferencia requisição finalizada, cancelamento e liberação não comprovada; neste último caso o programa bloqueia nova inferência.

O modo somente gravar não garante que software externo libere CPU/GPU/RAM, nem corrige falha de driver, falta de disco ou degradação da chamada por outra causa. Métricas de GPU e térmica não estão validadas. A visualização nativa e seu orçamento de energia/RSS ainda precisam de observação em uso real, fora de reunião.

## Validação e limites de evidência

A suíte usa fixtures sintéticas e servidores HTTP locais para validar cancelamento, subprocessos controláveis, importação supervisionada e cadência. `resource-safety-verification.json` é um registro histórico de uma execução, não prova automática para a revisão atual.

Testes reais anteriores com fala sintética portuguesa e um modelo Ollama local verificaram respostas funcionais sem abrir microfone nem reproduzir áudio. Tempos e capacidade dependem do hardware; não são uma garantia de desempenho. O monitor nativo possui testes headless de HTTP, Origin e leitura posterior, não certificação visual ou ensaio de reunião sob carga.

Para validar sob apresentação, medir avanço de áudio vs tempo de parede, gaps de captura e fluidez da chamada. Ensaios de pressão sintética ou alteração de roteamento requerem janela fora da reunião; testes unitários não substituem essa evidência.
