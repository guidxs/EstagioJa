# EstágioJá

Ferramenta web de automação que busca, filtra e organiza vagas de estágio em tecnologia.
Projeto acadêmico da disciplina de RPA com Python (CP2).

## Objetivo
Procurar estágios manualmente exige abrir várias páginas, ler requisitos e copiar links para
planilhas. O EstágioJá automatiza esse processo: consulta fontes externas, padroniza os
resultados, remove duplicatas, aplica filtros e exporta tudo em CSV.

Áreas de foco: Python e back-end, Data Science e análise de dados, engenharia de dados e
Big Data, e desenvolvimento de software em geral.

## Funcionalidades
- Busca por cargo, tecnologia ou palavra-chave
- Filtros por área, modalidade (remota, híbrida, presencial) e localização
- Opção "somente estágio" (reconhece estágio, estagiário, intern, internship, Praktikum, Werkstudent e trainee)
- Indicadores: vagas encontradas, empresas, vagas analisadas e data da consulta
- Cards com título, empresa, local, modalidade, resumo, fonte e link para a vaga original
- Exportação do resultado para CSV (separador `;`, abre corretamente no Excel)
- Tratamento de erros (timeout, sem conexão, HTTP inválido, JSON inválido, resposta vazia) e logs no terminal
- Modo demonstração com dados fictícios, claramente identificado

## Tecnologias
Python, Flask, requests, Jinja2, HTML5, CSS3 e JavaScript puro (apenas para a mensagem de carregamento).

## Fontes externas e limitações
| Fonte | Tipo | Chave de API | Observações |
|---|---|---|---|
| [GitHub Issues](https://github.com/frontendbr/vagas) (backend-br, pydevbr, frontendbr, nodejsdevbr, soujava, dotnetdevbr) | API REST oficial e pública | Opcional (`GITHUB_TOKEN`) | Vagas brasileiras publicadas como issues nas comunidades de desenvolvedores. Sem token, limite de 60 requisições/hora (o app usa 6 por atualização). |
| [Arbeitnow](https://www.arbeitnow.com) | API REST pública | Não | Foco em Europa; vagas remotas e presenciais. Atualizada de hora em hora. |
| [Remotive](https://remotive.com) | API REST pública | Não | Somente vagas remotas, com atraso de 24 h. Pede no máximo ~4 consultas por dia e exige citar a fonte e linkar a vaga original. |

Para respeitar esses limites, as respostas ficam em **cache local** na pasta `cache/`
(30 minutos para GitHub e Arbeitnow, 6 horas para a Remotive). Se uma fonte falhar, o app usa o cache
antigo ou segue apenas com as demais, sem travar.

### Por que não Gupy, InfoJobs, 99jobs e Eureca?
Foram verificados antes de escolher as fontes:
- **Gupy:** a API oficial exige token de empresa (empregador). O portal público usa um endpoint não documentado, sem permissão formal para uso por terceiros.
- **InfoJobs, 99jobs e Eureca:** não há API pública; só seria possível raspar o site, o que pode violar os termos de uso e esbarrar em proteções anti-bot.

Por isso o projeto coleta dados apenas de fontes com API pública e documentada.

**Atalhos para outras plataformas:** na tela de resultados, os botões *Buscar também em* (Indeed,
LinkedIn, Gupy e InfoJobs) abrem a busca já preenchida com os seus filtros em uma nova aba. Quem
faz a consulta é o usuário, no navegador: o EstágioJá não raspa nem coleta dados desses sites.
Os formatos dos links ficam na lista `EXTERNAL_SEARCHES` de `services/job_service.py`.

Limitações importantes:
- As vagas do GitHub são postadas pela comunidade: a **empresa** só é identificada quando aparece no título no formato "... na Empresa"; caso contrário aparece "Publicada por @usuário". A **cidade** vem do título (`[Cidade]`) e o país "Brasil" é presumido pelo perfil dos repositórios.
- Não há repositório de vagas para Data Science na lista; essa área vem principalmente das demais fontes.
- A modalidade "híbrida" e "remota" no GitHub é inferida pelo título, labels e início da descrição; "presencial" é o padrão quando nada é encontrado.
- Dados de terceiros podem mudar sem aviso. Sempre confirme na página original.

### Token do GitHub (opcional)
Para ampliar o limite de requisições, crie um token em GitHub > Settings > Developer settings
e defina a variável de ambiente antes de iniciar:
```powershell
$env:GITHUB_TOKEN="seu_token"; python app.py
```
O token nunca deve ser colocado no código nem enviado ao repositório.

## Instalação no Windows (VS Code)
1. Abra a pasta do projeto no VS Code e um terminal (`Ctrl + '`).
2. Crie e ative o ambiente virtual:
```powershell
py -m venv venv
venv\Scripts\Activate.ps1
```
   Se o PowerShell bloquear o script, rode antes:
   `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
   (no Prompt de Comando use `venv\Scripts\activate.bat`).
3. Instale as dependências:
```powershell
pip install -r requirements.txt
```
4. Inicie o servidor:
```powershell
python app.py
```
5. Abra http://127.0.0.1:5000 no navegador.

## Modo demonstração (dados fictícios)
Marque a caixa **Modo demonstração** no formulário ou inicie o app com a variável de ambiente:
```powershell
$env:DEMO_MODE="1"; python app.py       # PowerShell
set DEMO_MODE=1 && python app.py        # Prompt de Comando
```
Os dados vêm de `data/sample_jobs.json`, aparecem com a etiqueta "Fictício" e os links não levam a vagas reais.

## Exemplos de uso
- Estágio em Python no Brasil: busca `python`, localização `Brasil`, "Somente estágio" marcado.
- Estágio remoto em Python: busca `python`, modalidade *Remota*, "Somente estágio" marcado.
- Vagas de dados na Alemanha: área *Data Science*, localização `Germany`.
- Ampliar a busca: desmarque "Somente estágio" para ver vagas júnior e outras.
- Clique em **Exportar CSV** para baixar o resultado atual.

## Estrutura
```
EstágioJá/
├── app.py                  # rotas Flask
├── requirements.txt
├── services/job_service.py # integração com APIs, filtros, CSV
├── templates/              # base.html, index.html (Jinja2)
├── static/css/style.css
├── static/js/main.js
└── data/sample_jobs.json   # dados fictícios (só no modo demo)
```
