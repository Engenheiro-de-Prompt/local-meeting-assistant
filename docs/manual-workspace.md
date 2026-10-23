# Workspace manual e dashboard personalizável

## Escopo confirmado
O usuário aprovou anotações/checklists no topo, sem IA, salvos por reunião com rascunho anterior à gravação. Aprovou reorganizar/redimensionar blocos (não cada campo isolado), persistir o layout e restaurar o padrão. Não há publicação externa, nova dependência nem alteração do pipeline de inferência.

## Fatias e aceite
1. Conteúdo manual: texto livre e até 100 itens editáveis/marcáveis/removíveis; rascunho salvo no navegador, anexado somente ao início bem-sucedido da respectiva reunião; notas de reunião anterior não migram implicitamente. Sessões existentes continuam compatíveis.
2. Persistência local: notas no JSON da reunião, com revisão e identidade verificadas no servidor; edição em gravação/finalização/concluída. Conflitos entre abas não sobrescrevem silenciosamente; versões podem ser escolhidas explicitamente. Falhas remotas mantêm a edição no cache da própria aba quando o armazenamento está disponível. Se o navegador impedir a persistência ou recuperação, manter a cópia em memória, avisar para copiar o texto e impedir troca que abandone conteúdo não confirmado.
3. Layout: notas primeiro por padrão; blocos de controles, análises, configuração, transcrição e análises finais reposicionáveis/redimensionáveis por alças; caixas internas reorganizadas no seu grupo. Teclado como alternativa, layout local independente das reuniões e botão de restaurar somente o layout. Em telas estreitas, empilhar para manter usabilidade.

## Contratos e privacidade
- `manual_notes = {text, items:[{id,text,checked}]}` e `manual_notes_revision` pertencem à sessão, não à transcrição nem aos prompts.
- Atualização: POST `/api/manual-notes` com `{session_id,expected_revision,notes}`; comparar sessão e revisão antes do commit atômico da metadata. Nenhuma atualização manual solicita análise.
- Recursos JS/CSS servidos por allowlist exata e com a mesma proteção de origem do dashboard.
- Interface de teste: UI real com armazenamento do navegador e HTTP local; fixtures somente nas fronteiras de captura, transcrição e inferência. Exercitar persistência real em pasta temporária, nunca gravações privadas.
- No handoff, drenar todas as edições pendentes antes de iniciar/importar. Se o editor ainda aponta à reunião anterior, bloquear seus controles até o sucesso/falha da operação; rascunhos da próxima reunião continuam editáveis e são vinculados após sucesso.
- Cache de rascunhos: writer por documento e predecessor em sessionStorage; qualquer rascunho recuperado é copiado para o writer atual, mantendo recuperação após recargas consecutivas sem novas edições. Falha de cópia preserva o aviso de armazenamento e o conteúdo na página.
- Não afirmar validação nova de qualidade de IA: estes recursos não dependem de modelo.

## Fora de escopo
Editor de texto rico/HTML, colaboração remota, enviar notas ao modelo, sincronização em nuvem, biblioteca de reuniões antigas e exportação automática das notas em relatórios existentes.
