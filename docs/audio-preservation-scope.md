# Proteção do áudio — critérios de aceitação

A prioridade acordada é: chamada/apresentação, captura preservada, transcrição e análise. Fallback adia trabalho de IA, não autoriza apagar ou marcar como consumido áudio não transcrito. O áudio capturado é a fonte para recuperar a transcrição depois.

## Reforço implementado

A suíte inclui testes sintéticos de recuperação, preservação de chunks e integração Chrome. `audio-preservation-verification.json` registra uma execução histórica; não certifica automaticamente revisões posteriores nem implantação em outra máquina. Execute os comandos do README para validar a revisão em uso.

O worker de áudio consulta avanço a cada 2 s, solicita fsync do segmento ativo no máximo a cada 10 s e sincroniza cada segmento fechado antes do registro incremental em `.chunks/audio-journal.jsonl`. O painel distingue processo vivo de PCM avançando, mostrando duração e atraso. Avisos são exibidos na interface; não foi criado envio automático de alerta para Telegram.

A prevenção de suspensão por ociosidade foi verificada com uma asserção real do macOS vinculada ao PID do gravador e liberada ao terminar. Não foram alteradas as políticas globais de energia. Recuperação de WAV interrompido é uma operação offline explícita, em novo arquivo: `audio_safety.salvage_pcm_wav(origem, destino_novo)`; não modifica originais nem reconstitui períodos ausentes.

- Medir dados PCM efetivamente gravados e seu avanço no tempo, não apenas PID vivo.
- Distinguir silêncio válido (frames continuam chegando) de captura travada (não há avanço), mantendo aviso visível e suspendendo IA quando há risco de gravação.
- Flushing do FFmpeg e sincronização periódica do áudio em worker independente das inferências; pequenos checkpoints dos chunks fechados.
- Preservar segmentos originais; a união num arquivo final não pode ser a única oportunidade de conservar o áudio.
- Recuperação explícita em cópia de WAV interrompido: usar somente PCM efetivamente disponível e válido, sem modificar original, inventar amostras ou chamar resultado parcial de completo.
- Teste de falha real em FFmpeg sintético com SIGKILL, sem dispositivo, reprodução ou reunião. Comprovar conteúdo exato recuperável e integridade do segmento já fechado.
- Evitar suspensão automática por ociosidade durante a captura, sem alterar política global do Mac; não prometer proteção contra fechar a tampa, suspender manualmente ou desligar.

## Limites que não devem ser escondidos

`fsync`/flush reduzem janelas de buffer, mas não são garantia contra falha de energia, sistema de arquivos, firmware ou SSD. O último pedaço pode ser parcial. Uma segunda pasta no mesmo SSD não é redundância independente.

Frames avançando não provam que todas as pessoas da reunião foram capturadas, nem que o dispositivo selecionado tem roteamento correto. Confirmar fontes (microfone e áudio do sistema), nível de sinal e captura real continua necessário. Não confundir tempo total da sessão, que pode incluir processamento final, com duração real da captura.

Para maior proteção contra perda total, considerar uma segunda gravação em sistema/dispositivo independente. Gravação remota depende das permissões dos participantes e das políticas aplicáveis; este projeto não configura nem transmite uma cópia externa.

## Não declarar concluído sem evidência

- API/UI devem mostrar estado atual e avisos, não apenas checkpoints antigos.
- Testes determinísticos devem cobrir captura parada, lenta, silêncio, falhas de sync/journal e parada independente de IA.
- Teste real de processo morto não equivale a desligar o computador ou testar um encontro de horas sob carga.
- Após ativação, conferir estado sem gravação em andamento, serviço saudável e API atualizada. Não testar em gravações reais nem apagar originais.
