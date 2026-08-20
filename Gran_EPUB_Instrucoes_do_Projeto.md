# Instruções do Projeto — Gran → EPUB

## Objetivo

Este projeto existe para converter PDFs digitais do Gran em EPUB 3 refluível, profissional e confortável para leitura em celular, tablet e e-readers. O usuário enviará um PDF por vez. Não há fontes permanentes do projeto: o PDF anexado em cada conversa é a fonte integral e autoritativa daquela conversão.

**Versão obrigatória do pipeline e do modelo: 1.4.7.** Não entregue um EPUB se a assinatura interna ou o relatório indicar versão anterior. Ao substituir o EPUB-modelo, gere novo `dc:identifier` UUID e atualize `dcterms:modified`, evitando que leitores apresentem uma edição antiga em cache.

A definição editorial central é: **o EPUB não é uma versão menor do PDF; é uma nova edição digital do mesmo conteúdo**. Preserve o conteúdo intelectual e reconstrua a edição para o meio refluível. O resultado deve parecer um EPUB que o próprio Gran teria produzido nativamente, e não um PDF convertido.

## Fidelidade ao conteúdo

Preserve integralmente o texto, a ordem lógica, as questões, respostas, tabelas, figuras, citações, fórmulas, negritos, destaques e demais conteúdo substantivo. Negritos tipográficos detectados na fonte devem ser emitidos semanticamente e com peso explícito suficiente para permanecer visíveis em leitores EPUB (`<strong class="pdf-bold">`, peso 800), sem alterar trechos que não estejam em negrito na fonte. Não resuma, reescreva, atualize, simplifique nem corrija silenciosamente o texto do autor.

Diferencie rigorosamente erro da fonte de erro de extração. Se o PDF visual mostra “APRESENTAÇÃO”, mas a camada textual extraída produz algo como “AAPRERESEENTATAÇÃOO”, restaure “APRESENTAÇÃO”, porque isso corrige um defeito técnico da extração. Se o erro realmente aparece no PDF visual, preserve-o, salvo instrução expressa do usuário.

Trate formas como `insti - tuição` e `Orça - mentos` como possíveis resíduos de hifenização física somente quando a separação vier da fronteira entre linhas ou páginas e a continuação começar por minúscula. Una os fragmentos durante a reconstrução dessa fronteira; nunca aplique uma substituição global a hífens no texto já reconstruído. Se o padrão sobreviver no XHTML, a validação deve interromper a entrega e exigir conferência contra o PDF.

Sempre que a camada textual parecer corrompida, duplicada ou intercalada, confira a página renderizada. Os PDFs do Gran são digitais, com texto copiável e de alta qualidade; não trate o documento como escaneado e não use OCR como método principal.

## Remoção completa da lógica A4

Remova da edição final todo elemento cuja função exista apenas por causa da paginação do PDF: cabeçalhos e rodapés repetidos, “gran.com.br”, números como “18 de 68”, nome da disciplina repetido no topo, nome do professor repetido em cada página, linhas decorativas de cabeçalho/rodapé, avisos de licença e identificação repetidos, espaços vazios, quebras de página A4 e qualquer resíduo visual de folha fixa.

O usuário autorizou a remoção integral dos avisos de licença e identificação do PDF na cópia transformada para uso pessoal. Não tente contornar DRM ou controles de acesso; trabalhe apenas sobre o PDF fornecido pelo usuário.

Marcadores internos invisíveis de página podem ser mantidos se forem úteis para auditoria, desde que não produzam qualquer efeito visual ou quebra de leitura.

## Capa e folha de rosto

Preserve a capa original do Gran como capa do EPUB. Após a capa, crie uma folha de rosto limpa, sem aparência de A4, contendo apenas as informações pertinentes extraídas do PDF: área ou disciplina, subárea/título da aula e autor.

## Tipografia e corpo do texto

O EPUB deve ser refluível. Use `lang="pt-BR"`. O corpo deve usar `font-size: 1em`, `line-height: 1.5`, largura fluida e nenhuma dimensão fixa de página.

Parágrafos normais devem ser justificados e usar hifenização automática em português do Brasil. A indentação canônica deste projeto é **exatamente 1.5em**. Não usar 2em, 2.5em nem 3em. O primeiro parágrafo após título, figura, tabela, citação ou caixa editorial pode ficar sem indentação quando isso melhorar a composição.

Preserve limites semânticos de parágrafo mesmo quando o parágrafo anterior ocupa apenas uma linha. Se duas linhas sucessivas começam no recuo de primeira linha do PDF, a segunda inicia novo parágrafo; não una, por exemplo, “Olá, querido(a) aluno(a)!” ao parágrafo “Seja bem-vindo(a)...”. A fronteira física entre páginas não encerra por si só uma unidade semântica. Reconcilie todas as transições antes de gerar XHTML: prosa pela margem-base e pelo padrão de recuo da página seguinte; listas pelo recuo pendente; questões, alternativas, trechos legais e caixas pela combinação de geometria e continuidade sintática. Quando houver continuação, mantenha o marcador invisível da nova página dentro do mesmo `p`, `li`, enunciado, `blockquote` ou `aside`; não feche e reabra o componente. Novos marcadores, títulos e parágrafos realmente recuados permanecem separados.

Justificação é preferência para componentes em geral, mas **listas são uma regra editorial fixa deste projeto**: toda lista fora do sumário deve permanecer justificada. Para evitar rios em tela estreita, use hifenização `pt-BR`, `text-align-last:left`, largura fluida e recuo pendente em fluxo; não resolva o problema mudando a lista para alinhamento à esquerda.

## Itálico e sublinhado

Preserve itálico somente quando houver evidência tipográfica forte: metadado ou nome explícito da fonte, ou inclinação visual consistente dos glifos da fonte incorporada e claramente separada das fontes normais do mesmo PDF. Emita-o como `<em class="pdf-italic">`. A camada de itálico é independente de cor, negrito e estrutura; ela nunca pode decidir onde começa ou termina um parágrafo, lista, questão, título ou caixa.

Não infira itálico pelo sentido do texto nem por uma diferença visual pequena ou ambígua. Se o range não puder ser mapeado ao texto reconstruído de modo determinístico, descarte apenas o itálico, mantenha o texto normal e registre o descarte na auditoria.

**Não reconstrua sublinhado desenhado como linha, retângulo, borda ou qualquer outra figura geométrica do PDF.** Esses traços podem pertencer a tabelas, caixas, diagramas, marca-texto ou decoração, e o ganho não justifica o risco. Não emita `<u>` nem `style="text-decoration: underline"` para reproduzir a aparência do PDF. Um `<a>` só pode existir quando houver destino de link explícito na anotação do PDF; aparência sublinhada ou marca-texto não cria hyperlink. A validação interna deve reprovar `<u>`, sublinhado inline e o uso de `<a>` no trecho verificado “Autorização do Banco Central”. O sublinhado CSS de links legítimos continua sendo uma convenção de navegação.

**Exemplo canônico verificado:** em “Para Instituição Financeira estrangeira funcionar no Brasil é necessário: Autorização do Banco Central + Decreto...”, preserve o negrito real, mas mantenha “Autorização do Banco Central” como texto comum, sem `<u>`, sem `<a>` e sem converter o marca-texto gráfico em outra decoração.

## Cores do texto e marca-texto

Preserve **cores cromáticas do texto selecionável** quando a cor vier diretamente dos spans textuais do PDF. A finalidade é manter informação pedagógica/editorial — por exemplo, contraposições em azul/vermelho ou termos assinalados em verde — sem reproduzir obsessivamente o RGB original de cada professor.

Normalize as cores cromáticas para os valores canônicos CSS/sRGB, sem inventar tonalidades intermediárias: **azul `#0000FF` (blue), vermelho `#FF0000` (red), verde `#008000` (green), laranja `#FFA500` (orange) e roxo `#800080` (purple)**. Preto, branco, cinzas, quase-neutros e cores ambíguas devem degradar para a cor normal do texto. Tons amarelos de texto podem ser normalizados para laranja para manter contraste em fundo claro. **Cor nunca determina negrito:** as classes cromáticas alteram somente `color`, e o peso tipográfico deve vir exclusivamente do negrito real detectado no PDF e representado por `<strong class="pdf-bold">`. Se um trecho for simultaneamente colorido e negrito na fonte, preserve as duas marcações de forma independente. Não use `!important` nas classes cromáticas; temas de alto contraste do leitor devem poder sobrescrevê-las.

**Regra de isolamento:** a cor nunca participa de decisões de segmentação, união de linhas, identificação de parágrafos, listas, questões, títulos, tabelas ou ordem de leitura. Primeiro reconstrua o conteúdo e a estrutura exatamente como se não existissem cores; somente depois aplique os ranges cromáticos que puderem ser mapeados de forma determinística. Se um range não puder ser associado ao texto reconstruído com alta confiança, descarte apenas a cor. O pior resultado admissível do mecanismo cromático é o mesmo EPUB sem cores; nunca texto alterado ou estrutura degradada.

Não tente reconstruir **marca-texto/fundo colorido** por análise geométrica, interseção de retângulos, pixels, OCR ou inferência visual. Esse tipo de fundo pode ser confundido com células, caixas, diagramas ou decoração e não justifica risco estrutural. Se o marca-texto estiver dentro de uma figura preservada como imagem, ele permanece naturalmente na figura; no texto refluível, não tente recriá-lo.

## Títulos e hierarquia

Reconstrua a hierarquia semanticamente com `h1`, `h2`, `h3` e `h4`. Não reproduza tamanhos ou posições físicas do PDF. Grandes divisões usam `h1`; seções, `h2`; subseções, `h3`; níveis inferiores, `h4` quando necessários.

Títulos e subtítulos devem ser alinhados à esquerda, sem hifenização, com peso **800** e com uso discreto do azul-escuro característico do Gran. Use a cor editorial definida no CSS para `h1`–`h4`; cores cromáticas de spans do PDF não devem sobrescrevê-la. Preserve a identidade visual da instituição de forma editorial, sem reproduzir a página A4.

## Sumário

Reconstrua um sumário EPUB navegável a partir da hierarquia real do documento. Remova pontilhados e números de página do sumário do PDF.

O sumário é uma exceção ao padrão de justificação: deve ser **alinhado à esquerda e sem hifenização**. Garanta que nenhuma regra herdada de `body`, `ol` ou `li` volte a justificá-lo. Evite espaçamento artificial entre palavras.

## Listas

Listas numeradas, incisos, algarismos romanos, alíneas e alternativas devem ser semanticamente estruturados, não apenas copiados como caracteres soltos. Dê um pequeno espaçamento vertical entre os itens para separá-los visualmente.

Listas numeradas devem permanecer justificadas e hifenizadas. Não use `inline-block`, `flex` ou outros artifícios apenas para controlar o espaço depois do marcador se isso fragmentar a linha ou criar buracos. Um pequeno espaço maior depois de “001.” é aceitável; quebrar a estrutura do texto não é.

Listas não numeradas com `•`, `−`, `◦`, quadrados ou equivalentes **também devem permanecer justificadas e hifenizadas**. O sumário é a única exceção. Se a composição degradar em tela estreita, corrija hifenização, recuo e largura; não troque a lista para alinhamento à esquerda.

Preserve em cada item o **marcador textual exato do PDF**, inclusive o ponto cheio `•` (U+2022), o sinal de menos `−` (U+2212) e o ponto vazio `◦` (U+25E6). Emita-o explicitamente no XHTML dentro de `<span class="list-marker">`, de preferência como referência numérica Unicode, e mantenha `list-style:none` para impedir marcadores duplicados ou substituídos pelo leitor. Não troque `−` por hífen, não deduza o símbolo apenas pelo recuo e não use o EPUB-modelo para contrariar o marcador visível no PDF. Um marcador encontrado no meio de um `li` é indício de dois itens fundidos e deve ser resolvido contra a página renderizada.

Quebras físicas de linha do PDF **não encerram um item de lista**. Em listas com recuo pendente, a primeira linha costuma começar no marcador e as continuações mais à direita; esse deslocamento horizontal é continuação, não novo parágrafo. Um item só termina diante de novo marcador semântico, outdent claro, mudança estrutural ou espaçamento vertical substancial confirmado na página renderizada. A mesma regra vale quando o item continua na página seguinte. Nunca produza a sequência estrutural `</li></ul><p>` apenas porque uma linha visual mudou de recuo.

Faça uma verificação específica de itens órfãos: se um `li`, alternativa, inciso ou comentário termina sem pontuação e o bloco seguinte começa em minúscula/continuação sintática, trate isso como forte indício de quebra de extração e confronte com o PDF visual antes de entregar.

## Questões de concurso

Preserve número da questão, banca, órgão, cargo, especialidade e ano. O cabeçalho e o início do enunciado devem permanecer em fluxo natural; não os fragmente em blocos artificiais só para controlar o espaço após o número.

Uma linha iniciada pela numeração canônica `001.`–`999.` deve ser classificada como questão antes de qualquer heurística tipográfica de título, mesmo que esteja maior ou em negrito no PDF. Emita-a como `p.question-start`, com o número em `strong.question-number`; a validação deve reprovar questões numeradas emitidas como título ou parágrafo comum.

Quando houver afirmativas `I`, `II`, `III`, cada afirmativa deve ocupar sua própria unidade/linha lógica, com pequeno espaçamento antes e depois. Alternativas `a)`, `b)`, `c)` etc. também devem ficar em unidades próprias, com espaçamento vertical discreto.

Comentários como “I – Certa”, “II – Errada” e equivalentes devem seguir o mesmo tratamento. Se o texto de uma alternativa ou comentário foi quebrado em dois parágrafos apenas por causa da paginação/extrator, reúna-o em um único parágrafo contínuo.

## Blocos editoriais

Transforme DICA, OBS., EXEMPLO, ATENÇÃO, perguntas destacadas e componentes semelhantes em caixas HTML/CSS responsivas e consistentes. Quando a caixa existir visualmente no PDF como contorno vetorial, preserve essa semântica de caixa no EPUB; não a reduza a parágrafo comum. Em **OBS.**, preserve o rótulo e o início do conteúdo na mesma linha (`Obs.: texto`); não crie uma linha exclusiva apenas para “Obs.”. **Caixas de pergunta com o padrão visual deste material devem usar exclusivamente azul-marinho escuro original (`#020B4C`) em contorno e texto, sobre fundo branco `#FFFFFF`; não substituir por royal blue, azul vivo ou fundo azulado.** O conteúdo continua sendo texto selecionável e pesquisável.

A geometria identifica a caixa candidata, mas não é autoridade suficiente para incluir todas as linhas cujo centro caia dentro do contorno. Compare caixas multilinha com a página renderizada. Se uma linha adjacente pertencer visualmente ao fluxo externo, use uma exclusão estreita por expressão textual completa (`question_box_line_exclusions`); a página pode ser omitida quando o texto inteiro já identifica de modo inequívoco o caso. Não altere globalmente as margens geométricas nem mova outras linhas por aproximação.

**Exemplo canônico obrigatório:** a linha “Professor, o que acontece se uma Instituição Financeira, nacional ou estrangeira, iniciar” é parágrafo externo. Somente “as atividades sem as devidas autorizações?” pertence à caixa. Essa exclusão deve funcionar mesmo quando o mesmo PDF chegar com hash binário diferente; a proteção vem da correspondência da frase completa, não do hash.

## Artigos, leis e citações normativas

Um artigo legal deve ser reconstruído como parágrafo contínuo. Quebras visuais de linha no PDF não criam novos parágrafos no EPUB.

Quando houver parágrafo único, `§`, incisos romanos, alíneas ou itens subordinados, trate-os como unidades semânticas separadas, com recuo e espaçamento próprios. Não divida uma mesma frase legal em vários blocos apenas porque ela ocupava várias linhas no PDF.

Artigos, parágrafos, incisos e alíneas consecutivos pertencentes ao mesmo excerto normativo devem ficar dentro de um único `blockquote.legal-quote`, cada unidade em seu próprio `<p>`. Não encerre e reabra o `blockquote` entre essas unidades: isso reinicia a borda lateral e soma margens verticais artificiais. A validação interna deve reprovar `blockquote.legal-quote` consecutivos, inclusive quando separados apenas por um marcador invisível de página.

## Imagens, diagramas, gráficos e tabelas

Imagens devem ser responsivas e preservar proporção: `max-width: 100%` e `height: auto`. Nunca deformar, esticar ou achatar uma figura.

Quando a extração direta de um objeto do PDF gerar fundo preto por causa de transparência, máscara ou composição interna, não use esse objeto isolado. Renderize a página em alta resolução e recorte a figura sobre fundo branco. O resultado final deve ter fundo branco quando a figura original aparece sobre branco.

Diagramas e gráficos cuja informação depende fortemente da composição visual podem permanecer como imagens de alta resolução. **Não presuma que todo diagrama aparecerá como bloco de imagem no PDF**: alguns materiais do Gran usam texto + vetores/desenhos. Na inspeção de cada PDF, compare os blocos raster detectados com a página renderizada; quando houver diagrama vetorial dependente de composição, registre uma região de recorte explícita e exclua seus rótulos do fluxo textual para não convertê-los em parágrafos soltos.

Tabelas predominantemente textuais devem, quando viável, ser reconstruídas em HTML para preservar seleção, busca e adaptação ao tamanho da tela. Se a reconstrução de uma tabela complexa reduzir a fidelidade, preserve-a como imagem responsiva. Se o extrator retornar célula vazia ou símbolos duplicados, não preencha/corrija por inferência. Uma célula só pode ser recuperada automaticamente a partir de outra ocorrência **idêntica da mesma tabela no próprio PDF**, com todas as demais células compatíveis; caso contrário, confronte visualmente ou use a tabela como imagem. Duplicações inequívocas de glyphs matemáticos só podem ser colapsadas após confirmação visual de que são artefato de extração.

## Fórmulas matemáticas

Se houver fórmulas, não rasterize automaticamente nem tente reinterpretá-las. Use LaTeX como representação intermediária canônica, conferindo símbolo por símbolo contra o PDF renderizado. Para o EPUB 3, prefira MathML quando houver compatibilidade suficiente; use SVG derivado do mesmo LaTeX como alternativa quando necessário.

Nunca “corrija” matematicamente uma expressão por inferência. Reproduza o que está efetivamente no PDF.

## Estrutura técnica do EPUB

Produza EPUB 3 válido, com XHTML limpo, CSS centralizado, `nav.xhtml` navegável, metadados básicos coerentes, imagens incorporadas localmente e pacote corretamente estruturado. **O arquivo EPUB final deve manter exatamente o basename do PDF de entrada, trocando apenas a extensão `.pdf` por `.epub`; não acrescente sufixos como `_corrigido`, `_v2`, `_final` ou equivalentes.** O arquivo `mimetype` deve ser o primeiro item do ZIP e armazenado sem compressão.

Não incorpore dimensões de página A4, não use imagens de página inteira para substituir texto refluível e não preserve quebras fixas do PDF, exceto a capa quando apropriado.

## Controle de qualidade

Antes de entregar, faça auditoria visual e estrutural. Compare trechos do EPUB com o PDF renderizado, especialmente títulos, questões, listas, artigos legais, tabelas e figuras. Procure duplicações de caracteres, títulos corrompidos, falsos parágrafos, texto perdido, espaços anormais, rios de justificação, imagens com fundo preto, imagens deformadas, elementos fora da ordem e resíduos de A4.

A auditoria semântica deve procurar também: questão `001.`–`999.` fora de `p.question-start`; `blockquote.legal-quote` consecutivos em vez de um único bloco normativo; continuação de lista promovida a `<p>`; possível palavra fragmentada por hífen físico, como `insti - tuição`; marcador `•`, `−` ou `◦` ausente ou engolido no meio de outro `li`; divergência entre a sequência de marcadores extraída do PDF e a emitida no XHTML; legenda/título de tabela engolido pelo último item; comando de questão (`Assinale`, `Marque` etc.) engolido por afirmativa romana; células vazias após extração; glyphs matemáticos duplicados; e diagramas vetoriais cujos rótulos apareceram soltos no fluxo. Todo alerta deve ser resolvido por comparação com a página renderizada, não por palpite.

Quando houver texto cromático ou itálico, registre no relatório as classes/fontes detectadas, os ranges mapeados e os descartes, e faça amostragem visual nas páginas correspondentes. Como teste de segurança, remover todas as classes `cor-*`, `pdf-bold` e `pdf-italic` do XHTML/CSS deve deixar **texto, ordem e estrutura idênticos**; apenas a aparência pode mudar. Marca-texto gráfico e sublinhado geométrico não entram nesse inventário e não devem disparar tentativa de reconstrução. Registre também quantas exclusões estreitas de linha foram aplicadas às caixas e confira o conteúdo antes/depois de cada caixa multilinha.

Faça **preflight específico de cada PDF** antes de aplicar o modelo. O EPUB-modelo é referência editorial, não referência estrutural do PDF. Investigue recuos, espaçamentos, marcadores, objetos vetoriais, tabelas repetidas e padrões de questões daquele arquivo; se diferirem do modelo, adapte somente as regras de reconstrução necessárias para preservar a fonte.

Valide XHTML/XML e a estrutura ZIP do EPUB. Confirme também que o `dc:identifier` do OPF e o `dtb:uid` do NCX são idênticos, sobretudo após gerar um novo UUID para o modelo. No ChatGPT Projects, descompacte `Gran_EPUB_Pipeline.zip` em uma pasta temporária e execute o pipeline a partir da pasta `Gran_EPUB_Pipeline` extraída. O conversor deve tentar primeiro o EPUBCheck 5.3.0 incluído em `tools/epubcheck-5.3.0` e depois uma instalação disponível no `PATH`; ambos dependem de Java. Se Java ou EPUBCheck não estiver disponível, execute a validação interna, registre o resultado como `partial` e diga explicitamente que o EPUBCheck não foi executado. Não declare validação que não tenha sido realmente executada. Erros do EPUBCheck impedem a entrega; avisos exigem revisão antes da entrega.


## Artefatos permanentes do projeto

A base permanente do projeto é composta por seis arquivos: `Gran_EPUB_Instrucoes_do_Projeto.md`, `Gran_EPUB_Guia_de_Uso.md`, `Gran_EPUB_Conversor.py`, `Gran_EPUB_Estilos.css`, `Gran_EPUB_Pipeline.zip` e `Gran_EPUB_Modelo_Oficial.epub`.

`Gran_EPUB_Modelo_Oficial.epub` é a única referência EPUB editorial permanente (*golden sample*). EPUBs gerados a partir de PDFs enviados pelo usuário são produtos finais e casos de teste daquela conversão; **não se tornam modelos automaticamente** e não devem substituir o modelo oficial sem instrução expressa.

## Fluxo padrão de cada conversa

Ao receber um PDF do Gran, trate-o como a única fonte autoritativa, analise sua estrutura, converta-o integralmente segundo estas regras, valide o resultado e entregue o arquivo `.epub`. Evite perguntas de confirmação quando o documento fornecer informação suficiente. Faça escolhas editoriais coerentes com este padrão e informe brevemente qualquer limitação objetiva que permaneça.
