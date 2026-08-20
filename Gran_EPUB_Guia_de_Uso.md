# Gran → EPUB: pipeline-base

Este pacote é a parte **processual** do projeto Gran → EPUB. O EPUB-modelo mostra o resultado final; este código mostra como partir de um PDF digital e chegar a um EPUB refluível.

**Versão vigente: 1.4.7.** O XHTML gerado contém uma assinatura interna de versão. O EPUB-modelo usa identificador UUID próprio desta revisão para impedir que leitores reutilizem uma edição anterior em cache.

## O que o pipeline faz

1. Abre o PDF digital com PyMuPDF.
2. Lê spans, coordenadas, fonte, tamanho, posição, cor textual sRGB e peso tipográfico. Os negritos reais do PDF são preservados como marcação semântica `<strong class="pdf-bold">`, com peso 800 explícito para compatibilidade entre leitores; nos PDFs do Gran, quando a fonte variável não declara corretamente o peso, o pipeline distingue as instâncias pelo desenho efetivo dos glifos incorporados, sem OCR. Itálicos só são preservados como `<em class="pdf-italic">` quando o nome/metadado é explícito ou a inclinação dos glifos incorporados é forte, consistente e claramente separada das fontes normais. Cores cromáticas são classificadas por matiz e mapeadas para a paleta CSS/sRGB canônica (`#0000FF`, `#FF0000`, `#008000`, `#FFA500`, `#800080`) somente depois da reconstrução estrutural; preto/cinza/quase-neutros são ignorados. Cor, negrito e itálico permanecem camadas independentes e nunca alteram a estrutura.
3. Remove duplicações de spans sobrepostos — o caso que gerava `AAPRERESEENTATAÇÃOO`, por exemplo.
4. Detecta cabeçalhos/rodapés repetidos e remove resíduos de A4.
5. Estima a fonte de corpo e reconstrói parágrafos a partir de posição, recuo e distância vertical. Uma linha no recuo de primeira linha inicia novo parágrafo mesmo quando o parágrafo anterior tinha apenas uma linha; assim, saudações como “Olá, querido(a) aluno(a)!” não engolem o parágrafo seguinte. Entre páginas consecutivas, reconcilia a última unidade da página anterior com o primeiro bloco da seguinte antes de gerar XHTML: prosa usa margem-base e evidência do padrão de recuo; listas usam o recuo pendente; questões, alternativas, trechos legais e caixas usam simultaneamente geometria e continuidade semântica. Na fronteira física entre linhas ou páginas, remove a hifenização quando o fragmento termina em `palavra-` ou `palavra -` e a continuação começa por minúscula; a regra não faz substituição global dentro de texto já reconstruído, preservando hífens legítimos. O marcador EPUB da nova página permanece invisível dentro da mesma unidade (`p`, `li`, enunciado, citação ou `aside`), sem fechar e reabrir o componente. Novos marcadores semânticos, títulos e parágrafos realmente recuados continuam separados.
6. Reconhece heurísticamente títulos, questões `001.`, alternativas `a)`, enumerações `I –`, bullets, artigos e parágrafos legais, com preflight semântico para detectar continuações órfãs e trechos engolidos por unidades anteriores. A numeração canônica `001.`–`999.` tem prioridade sobre tamanho e negrito: toda questão deve sair como `p.question-start`, e a validação reprova um marcador desse padrão emitido como título ou parágrafo comum. Artigos, parágrafos, incisos e alíneas consecutivos do mesmo excerto são emitidos como parágrafos internos de um único `blockquote.legal-quote`; a validação rejeita caixas legais consecutivas que reiniciariam a borda e as margens. Caixas editoriais de pergunta desenhadas no PDF como contornos arredondados são detectadas geometricamente e mantidas como caixas HTML/CSS refluíveis, sem converter o conteúdo em imagem. A geometria define a caixa candidata; exceções por página e texto exato podem retirar uma linha adjacente quando a página renderizada mostrar que ela está fora da caixa.
7. Usa `page.find_tables()` com detecção de linhas estrita para reconstruir tabelas textuais em HTML quando possível, reduzindo o risco de perder a primeira/última coluna. O QA também procura tokens alinhados fora da borda detectada. Células vazias não são inferidas; só podem ser recuperadas de outra ocorrência idêntica da mesma tabela no PDF.
8. Localiza imagens e as **renderiza por recorte da página sobre fundo branco**, em vez de confiar cegamente na extração do objeto PDF. Isso evita o fundo preto causado por máscaras/transparência. Diagramas compostos apenas por vetores/texto exigem `image_regions` após inspeção visual.
9. Cria capa, folha de rosto, XHTML refluível, CSS, sumário navegável e marcadores invisíveis de páginas. Em caixas **OBS.**, mantém rótulo e conteúdo na mesma linha (`Obs.: texto`). Caixas de pergunta do padrão Gran usam azul-marinho escuro `#020B4C` e fundo branco, sem tons inventados.
10. Valida XML e estrutura ZIP; procura primeiro o EPUBCheck 5.3.0 incluído no pacote e depois um executável no `PATH`. A execução do EPUBCheck depende de Java.
11. Gera um `.audit.json` com alertas e pontos que merecem inspeção visual, incluindo fragmentos de lista, legendas engolidas, comandos de questão anexados a afirmativas, células vazias e estatísticas de ranges cromáticos.

## Regra editorial congelada

- Corpo: `1em`, entrelinha `1.5`, justificado, hifenização PT-BR.
- Indentação normal: **1.5em, exatamente**.
- Títulos: esquerda, sem hifenização, `font-weight: 800`, azul-escuro Gran. Cores cromáticas extraídas do PDF não sobrescrevem a cor editorial de `h1`–`h4`.
- Sumário: esquerda, sem hifenização.
- Listas: **todas** (numeradas, não numeradas, alternativas, romanos, incisos e gabaritos) ficam justificadas e hifenizadas em `pt-BR`. Use `text-align-last:left`, recuo pendente em fluxo e evite `flex`/`inline-block`. Marcadores como `•`, `−` e `◦` são preservados explicitamente no XHTML, com o mesmo caractere e a mesma hierarquia do PDF; não dependa do marcador automático do leitor.
- O **sumário** é a única exceção: alinhado à esquerda e sem hifenização.
- Imagens: `max-width: 100%`, `height: auto`, proporcionais.
- Artigo legal: um parágrafo corrido; §, incisos e alíneas são unidades semânticas próprias.
- Cores textuais: paleta CSS/sRGB canônica — azul `#0000FF`, vermelho `#FF0000`, verde `#008000`, laranja `#FFA500`, roxo `#800080` — somente quando derivadas diretamente dos spans do PDF. As classes cromáticas alteram somente a cor; negrito é preservado separadamente apenas quando pertence ao texto no PDF.
- Itálico: preservar apenas com evidência tipográfica inequívoca e emitir `<em class="pdf-italic">`; em dúvida, manter normal e registrar o descarte.
- Sublinhado: **não reconstruir a partir de linhas ou figuras geométricas**. O sublinhado de links permanece apenas como convenção de navegação.
- Marca-texto/fundos coloridos: **não reconstruir por geometria ou pixels**; preservar apenas quando já fizerem parte de uma figura mantida como imagem.

## Segurança da camada cromática

A camada de cor é propositalmente desacoplada da reconstrução. O pipeline produz primeiro o mesmo texto e a mesma estrutura que produziria sem cores; depois tenta projetar os ranges cromáticos sobre o texto final. Mapeamentos ambíguos são descartados. Isso evita que diferenças de paleta entre professores alterem parágrafos, listas, questões ou ordem de leitura.

A classificação usa matiz/saturação para identificar a família cromática, mas a saída usa **somente** os RGBs canônicos definidos no CSS. Assim, um azul detectado vira `#0000FF`, e não royal blue ou outro tom inventado; o mesmo vale para vermelho, verde, laranja e roxo. Tons neutros e quase-neutros permanecem com a cor normal do leitor. A configuração `preserve_text_colors` pode ser desligada por override para diagnóstico; desligá-la não deve mudar nenhum caractere nem elemento estrutural.

No preflight de um PDF novo, inventarie as cores dos spans e confira algumas páginas cromáticas. Não tente deduzir marca-texto amarelo a partir de retângulos ou fundos gráficos.

## Segurança das camadas tipográficas

Negrito e itálico são aplicados somente depois da reconstrução do texto. Um mapeamento incerto perde apenas o estilo, nunca caracteres, ordem ou estrutura. O relatório registra fontes detectadas, runs vistos, preservados e descartados. A remoção das marcações `pdf-bold` e `pdf-italic` deve produzir o mesmo texto e o mesmo DOM estrutural.

O pipeline deliberadamente ignora sublinhados formados por traços vetoriais. Eles não são candidatos a ranges textuais porque podem ser bordas, tabelas, caixas, marca-texto ou partes de diagramas. A validação interna reprova `<u>`, `text-decoration: underline` inline e o uso de hyperlink sobre o trecho verificado “Autorização do Banco Central”. Links legítimos exigem destino explícito na anotação do PDF.

## Uso

```bash
python Gran_EPUB_Conversor.py inspect entrada.pdf inventario.json
python Gran_EPUB_Conversor.py convert entrada.pdf saida.epub
python Gran_EPUB_Conversor.py validate arquivo.epub
```

Com ajustes específicos:

```bash
python Gran_EPUB_Conversor.py convert entrada.pdf saida.epub --overrides overrides.json
```

O `overrides.example.json` mostra o formato. Em uso dentro do projeto do ChatGPT, o modelo pode gerar ou ajustar `overrides.json` depois de inspecionar o PDF; o usuário não precisa preencher isso manualmente. Para uma linha que a geometria incluiu indevidamente numa caixa, use `question_box_line_exclusions` com `pattern` ancorado ao texto completo e, quando necessário, `page`. A exceção canônica da pergunta “Professor...” usa a frase completa e independe do SHA-256, para continuar funcionando quando o Projects recebe outra representação binária do mesmo PDF; por ser textual e exata, ela não alcança frases diferentes.

### Uso no ChatGPT Projects e EPUBCheck

Antes de executar o pipeline, descompacte `Gran_EPUB_Pipeline.zip` em uma pasta temporária e rode o conversor a partir da pasta `Gran_EPUB_Pipeline` extraída. O pacote contém a distribuição oficial completa do EPUBCheck 5.3.0 em `tools/epubcheck-5.3.0`, mas o ambiente também precisa oferecer o comando `java`.

O relatório diferencia três resultados: `passed` quando o EPUBCheck foi executado e aprovou o arquivo; `failed` quando a validação encontrou erro; e `partial` quando somente a validação interna de ZIP, `mimetype`, CRC, XML e consistência dos identificadores OPF/NCX pôde ser executada. Nunca descreva `partial` como aprovação pelo EPUBCheck. Erros impedem a entrega; avisos devem ser examinados contra o PDF antes da entrega.

## Por que existe `inspect`

PDF não é um formato semântico. Dois arquivos visualmente idênticos podem ter estruturas internas muito diferentes. `inspect` gera um inventário com linhas, bboxes, estilos, imagens e tabelas. Isso permite ao modelo identificar problemas antes de modificar o pipeline por tentativa e erro.

### Preflight obrigatório por PDF

O EPUB-modelo define o padrão editorial, mas **não** define a estrutura interna dos PDFs futuros. Antes de converter um arquivo novo, renderize amostras e compare com o inventário: recuo de corpo, recuo pendente de listas, gaps verticais, padrões de questões, tabelas e presença de vetores. Se a página renderizada contém um diagrama que não aparece em `images`, crie uma região explícita em `image_regions`; os rótulos dentro dela devem ser excluídos do fluxo para evitar parágrafos soltos.

O QA deve falhar/alertar quando encontrar questão `001.`–`999.` fora de `p.question-start`, `blockquote.legal-quote` consecutivos, `li` aparentemente truncado seguido de parágrafo em minúscula, possível palavra fragmentada no padrão `insti - tuição`, marcador `•`, `−` ou `◦` incorporado no meio de outro item, divergência entre a sequência de marcadores do PDF e a emitida no XHTML, `Tabela N:` dentro do último item, comando como `Assinale`/`Marque` dentro de afirmativa romana, célula vazia, duplicação matemática suspeita ou sublinhado textual sem link explícito no PDF. Esses sinais são investigados contra o PDF visual; não são corrigidos por inferência. Para cor, negrito e itálico, confira o inventário e faça o teste de remoção das marcações: o DOM textual/estrutural deve permanecer equivalente. Para caixas multilinha, compare o texto imediatamente anterior, interno e posterior com a página renderizada e registre exclusões aplicadas. No caso canônico, “Professor...” fica fora da caixa e apenas “as atividades...” fica dentro.

## Limite importante

Este código é uma **base determinística e auditável**, não uma promessa de conversão universal perfeita. O Gran produz disciplinas e layouts diversos. Diagramas vetoriais, fórmulas complexas, caixas incomuns ou tabelas excepcionais podem exigir um override ou uma pequena extensão do classificador.

A regra é não degradar silenciosamente: se a heurística estiver incerta, o `.audit.json` deve orientar a conferência contra a página renderizada.

## Matemática

O pipeline-base não tenta converter fórmulas automaticamente, porque inferir matemática incorretamente é pior que preservar a fonte. Para PDFs matemáticos, a extensão planejada é:

`PDF → região matemática → LaTeX intermediário → comparação visual → MathML ou SVG`.

O código deve tratar LaTeX como representação de trabalho, não como fonte de verdade; o PDF visual permanece autoritativo.


### Nome do arquivo de saída

O EPUB final conserva exatamente o basename do PDF de entrada e troca apenas a extensão por `.epub`. Exemplo: `Sistema Financeiro Nacional.pdf` → `Sistema Financeiro Nacional.epub`. O conversor ignora nomes alternativos passados no argumento de saída e usa esse basename no diretório solicitado.

## Arquivos permanentes do Projeto

Mantenha exatamente estes seis artefatos como base permanente do projeto:

- `Gran_EPUB_Instrucoes_do_Projeto.md` — regras editoriais e de QA.
- `Gran_EPUB_Guia_de_Uso.md` — documentação operacional.
- `Gran_EPUB_Conversor.py` — conversor-base determinístico.
- `Gran_EPUB_Estilos.css` — folha de estilos canônica.
- `Gran_EPUB_Pipeline.zip` — pacote operacional do pipeline.
- `Gran_EPUB_Modelo_Oficial.epub` — *golden sample* editorial oficial, baseado no material de Sistema Financeiro Nacional.

EPUBs produzidos em conversões futuras são **saídas**, não modelos e não arquivos permanentes do projeto. Eles não devem substituir `Gran_EPUB_Modelo_Oficial.epub` salvo instrução expressa do usuário.

`overrides.json` e inventários/auditorias são artefatos temporários ou específicos de um PDF e não precisam permanecer na memória do projeto.
