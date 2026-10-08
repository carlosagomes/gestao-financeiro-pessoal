# Gestão Financeira Pessoal

Controle financeiro pessoal multiusuário: lançamentos do mês, notas do Nota Paraná item por item, contas de água (Sanepar) e luz (Copel) conferidas sozinhas, e o módulo PJ para quem tem ele ligado.

Feito em Django + PostgreSQL. Ele substituiu o app antigo em Streamlit, que foi removido em 06/10/2026. O backup do código e dos dados antigos está em `dados/backups/sistema-antigo-2026-10-06.tar.gz`.

## Acessar

```bash
cd web
docker compose up -d --build          # banco + site + trabalhador
```

- Abra **http://localhost:8000**.
- Entre com a sua conta, já com todos os dados do sistema antigo.
- O e-mail e a senha provisória estão em `web/dados/acesso-inicial.txt`, que só o seu usuário do Mac consegue ler.
- Troque a senha em **Perfil → Trocar senha** e depois apague o arquivo.
- A conta `demo@gfp.local` é uma cópia dos dados para testes; pode excluí-la em Administração.

## O que tem

| Página | O que faz |
|---|---|
| **Resumo** | Entradas, saídas pagas, saldo e "a pagar" do ano. Tabela por mês colorida, com TOTAL ANUAL. Clique num mês ou numa categoria para ver os lançamentos. |
| **Lançamentos** | A planilha do mês: edita direto na tabela e salva sozinho. Também marca pagas, copia o mês anterior e tem histórico de tudo que mudou. |
| **Notas Paraná** | Placar, consumo por categoria, onde e como pagou. Clique numa barra para abrir a linha do tempo. Mostra a nota completa e as regras de categoria. |
| **Produtos** | Todos os produtos, o que mais subiu ou caiu de preço, e o histórico de preço por loja. |
| **Água · Sanepar / Luz · Copel** | Faturas, consumo e conferência com o lançamento do mês, além de baixar e enviar o PDF da conta. |
| **Horas e informes (PJ)** | Informe mensal, conferência com o Salário e previsão até dezembro. **Só aparece para quem tem o módulo PJ ligado** (Administração → Usuários → "módulo PJ"). |
| **Configurações** | CPF e senha do Nota Paraná, Sanepar e Copel (cifrados), sincronizar agora, conectar (janela remota) e histórico das sincronizações. |
| **Perfil** | Nome, e-mail, horário da atualização automática, troca de senha e exclusão da conta com todos os dados. |

### Atualização automática e janela remota

- **Nota Paraná:** o sistema entra sozinho com CPF e senha todo dia, no horário escolhido no Perfil.
- **Sanepar e Copel:** pedem captcha e código, que **você** resolve.
  - Em Configurações, **Conectar** abre o navegador do servidor dentro da sua tela. A senha já vem preenchida; você marca "Não sou um robô" e digita o código.
  - Depois disso o sistema busca as faturas sozinho.
  - A sessão da Sanepar dura dias. A da Copel dura minutos, então conecte quando a conta nova sair; o sistema avisa a data.
- **Alternativa sem login:** mande o PDF da conta pelo botão **Enviar PDF**.

## Segurança

- **Contas:** o login é por e-mail, com senha guardada em Argon2. Trocar o e-mail pede a senha atual.
- **Força bruta:** o login bloqueia por 15 minutos depois de 8 erros no mesmo e-mail e IP, 20 erros na mesma conta (de qualquer IP) ou 30 erros vindos do mesmo IP. O `/admin/` entra pelo mesmo login. O cadastro aceita até 5 contas novas por IP por hora.
- **Senhas dos sites:** CPF, senhas dos sites e sessões salvas ficam **cifrados** no banco (Fernet, chave `APP_ENCRYPTION_KEY` no `.env`). Nunca aparecem na tela nem nos registros.
- **Isolamento:** cada usuário só vê e só altera os próprios dados. O teste `core/tests.py` passa por **todas** as rotas como outro usuário, lendo e gravando com os ids da vítima, e falha se surgir rota nova sem essa verificação.
- **Navegador:** cabeçalhos de segurança em toda resposta (CSP, sem frames, sem envio de dados para outros sites). Os textos que vêm das notas (lojas, produtos) são escapados antes de ir para a tela.
- **Arquivos:** PDFs e páginas das notas ficam fora da web, só saem por views que conferem o dono e são gravados legíveis só pelo sistema (0600).
- **Containers:** site e trabalhador rodam sem root (usuário `app`, uid 1000), inclusive o Chromium da janela remota.
- **Exclusão:** "Excluir minha conta" apaga tudo da pessoa, inclusive os arquivos no disco.
- **Chave de cifragem:** **guarde o `web/.env` com cuidado**. Sem a `APP_ENCRYPTION_KEY`, as senhas salvas não podem ser lidas e precisam ser cadastradas de novo.

## Publicar na internet

1. **Servidor:** um com Docker, por exemplo uma VPS de 2 GB de RAM. Cada janela de conexão aberta usa cerca de 300 MB.
2. **Arquivos:** copie a pasta `web/` e o `.env`. Gere chaves novas para produção (veja o `.env.example`).
3. **`.env`:**
   - `DJANGO_DEBUG=0` e `DJANGO_HTTPS=1`;
   - `DOMINIO=financas.seudominio.com.br`;
   - `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` com o domínio;
   - `APP_CADASTRO_ABERTO=0` se quiser cadastro só por convite.
4. **Permissões:** os containers rodam com o uid 1000. Num servidor Linux, antes de subir, dê a pasta de dados a ele: `sudo chown -R 1000:1000 dados`.
5. **Subir:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`. O Caddy pega o certificado HTTPS sozinho.
6. **Uma instância por vez:** só deixe **um** trabalhador rodando por conta. O Nota Paraná aceita uma sessão por vez, então dois atualizadores com a mesma conta derrubam a sessão um do outro.

## Desenvolvimento

Veja o [GUIA.md](GUIA.md): estrutura, regras do código, componentes e como testar.

```bash
docker compose up -d db
.venv/bin/python manage.py runserver 8000
.venv/bin/python manage.py test                       # 185 testes (os exemplos reais ficam em testes_dados/, fora do git)
.venv/bin/python manage.py importar_legado --email voce@exemplo.com --pasta /caminho/do/backup-extraido   # dados do app antigo
```
