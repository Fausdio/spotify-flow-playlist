# spotify-flow-playlist

Cria (ou remixa) playlists do Spotify ordenadas como um set de DJ — BPM
vizinho e tom compatível — pra soar como uma faixa contínua quando você
ativa o **Crossfade + Automix** no Spotify desktop.

Funciona para **qualquer artista** ou **qualquer playlist já existente**.

Dados de BPM/tom, quando não disponíveis via Spotify, são complementados
opcionalmente pela API pública da [GetSongBPM.com](https://getsongbpm.com/) —
usada aqui via `--use-getsongbpm`. 🔗 Powered by [GetSongBPM.com](https://getsongbpm.com/).

## Por que não é um bot clicando no navegador?

Duas coisas que valem saber antes de usar:

1. **O crossfade/Automix não existe no Spotify Web Player** — só no app
   desktop e mobile. Automatizar o Chromium contra `open.spotify.com`
   não teria como acionar essa mixagem de jeito nenhum, porque o
   controle simplesmente não está lá.
2. **Criar/reordenar playlist é bem mais confiável pela API oficial**
   do que clicando na UI: não quebra quando o Spotify muda o layout, e
   é o uso que a própria Spotify documenta e disponibiliza de graça pra
   qualquer desenvolvedor.

Então este programa faz a parte que dá pra automatizar de verdade (achar
as melhores faixas, pegar BPM/tom, montar a ordem, criar a playlist) e
te avisa no final pra ativar o crossfade manualmente — é um toggle único
que depois vale pra qualquer playlist.

## Configuração (uns 5 minutos, só na primeira vez)

1. **Crie um app gratuito no Spotify:**
   - Acesse https://developer.spotify.com/dashboard e faça login
   - "Create app" → dê qualquer nome/descrição
   - Em **Redirect URI**, adicione exatamente: `http://127.0.0.1:8888/callback`
   - Marque a API "Web API" quando pedir
   - Salve e copie o **Client ID** e o **Client Secret**

2. **Instale as dependências:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure as credenciais:**
   ```bash
   copy .env.example .env
   ```
   Abra o `.env` e cole seu `SPOTIFY_CLIENT_ID` e `SPOTIFY_CLIENT_SECRET`.

4. **(Opcional) BPM de reserva:** a Spotify bloqueia o endpoint de
   BPM/tom (`audio-features`) para apps criados depois de nov/2024 —
   é uma restrição deles, sem workaround oficial. Se seu app cair nessa
   restrição (o programa avisa na hora), você pode registrar uma chave
   grátis em https://getsongbpm.com/api e colar em `GETSONGBPM_API_KEY`
   no `.env`. Sem isso, a playlist ainda é criada, só que ordenada por
   popularidade em vez de BPM/tom exatos.

## Uso

Melhores músicas de um artista (discografia inteira, não só o Top 10):
```bash
python main.py --artist "Twenty One Pilots" --top 30
```

Remixar a ordem de uma playlist que você já tem:
```bash
python main.py --playlist "https://open.spotify.com/playlist/SEU_ID_AQUI"
```

Só ver a ordem sugerida sem criar nada na sua conta ainda:
```bash
python main.py --artist "Twenty One Pilots" --top 30 --dry-run
```

Com fallback de BPM via getsongbpm.com:
```bash
python main.py --artist "Twenty One Pilots" --top 30 --use-getsongbpm
```

Outras opções: `--name "Nome da playlist"`, `--public` (padrão é privada).

Testando com mais de uma conta Spotify? Use `--account apelido` pra cada
uma guardar seu próprio login em cache (`.cache-flowlist-apelido`), sem uma
sobrescrever o token da outra:
```bash
python main.py --artist "Twenty One Pilots" --top 30 --dry-run --account conta2
```
Lembrando: enquanto o app estiver em "Development Mode" no dashboard, cada
conta usada aqui precisa estar em **Settings → User Management**, senão a
autorização falha.

**O rate limit da Spotify é por app (Client ID), não por conta** — confirmado
na prática: duas contas diferentes autorizadas no mesmo app bateram no mesmo
limite. Se isso acontecer e você não quiser esperar o `Retry-After` passar,
crie um **segundo app** no dashboard (é gratuito, sem limite de quantos apps
você pode ter) e use `--env-file` pra apontar pra ele:
```bash
copy .env.example .env.app2
# edite .env.app2 com o Client ID/Secret do app novo
python main.py --artist "Twenty One Pilots" --top 30 --dry-run --env-file .env.app2
```
Isso já separa o cache de login automaticamente (`.cache-flowlist-app2`), sem
precisar passar `--account` também.

Na primeira execução, o navegador abre pra você aprovar o app na sua
conta Spotify (OAuth) — normal, é o mesmo fluxo de "logar com Spotify"
de qualquer app de terceiros. O token fica salvo em `.cache-flowlist`
pras próximas vezes.

## Depois de criar a playlist

No **Spotify desktop**:
1. Clique na sua foto → **Configurações**
2. Em **Reprodução de música**, ative **Crossfade das músicas** e arraste
   o slider pro máximo (~12s)
3. Logo abaixo, ative **Automix**
4. Abra a playlist criada, deixe o **shuffle desligado** e toque

## Limitações honestas

- **audio-features da Spotify:** bloqueado por padrão pra apps novos
  desde nov/2024. O programa detecta e avisa; não é bug.
- **Endpoints "vários de uma vez" (`GET /v1/albums?ids=`, `GET /v1/tracks?ids=`):**
  também voltam 403 pra apps novos, mesmo pedindo 1 ID só — confirmado
  testando na prática. Por isso `get_artist_best_tracks` busca álbum por
  álbum e faixa por faixa (mais lento, mas funciona).
- **`artist_albums(..., limit=...)`:** o limite documentado é 50, mas na
  prática qualquer valor acima de 10 dá 400 "Invalid limit" pra apps
  novos. O código já usa `limit=10`.
- **Rate limit:** apps novos levam rate limit bem mais cedo que o
  esperado — em teste, uma sequência de chamadas (inclusive as de
  diagnóstico acima) resultou num `Retry-After` de ~23h. O spotipy por
  padrão **dorme esse tempo inteiro em silêncio** antes de tentar de
  novo; o `cli.py` desliga isso (`status_retries=0`) e falha na hora com
  uma mensagem clara em vez de travar o programa o dia todo. Se isso
  acontecer, espere o tempo indicado antes de rodar de novo — não é bug,
  é o limite da própria Spotify.
- **getsongbpm.com (fallback):** é uma API pública de terceiros, os
  nomes exatos dos campos podem variar por conta. Rode com `--debug-bpm`
  pra ver a resposta crua se os dados vierem estranhos, e ajuste
  `flowlist/enrichment.py::_parse_getsongbpm_song` se precisar.
- **Crossfade/Automix:** não existe API pra isso em nenhuma plataforma.
  Sempre manual, sempre no desktop/mobile.
