# iPhone Manager

**Um gerenciador de iPhone gratuito e de código aberto para Windows.** Veja e exporte suas fotos (inclusive o
que está só no iCloud), gerencie apps e arquivos, veja e controle a tela do iPhone pelo PC e atualize o iOS
sem apagar nada, tudo num app local no navegador.

[English](README.md) · Português (Brasil)

![Visão geral](docs/screenshots/overview-pt.png)

> As imagens vêm do [modo demonstração](#modo-demonstração), com dados fictícios e fotos livres do
> [Lorem Picsum](https://picsum.photos) / [Unsplash](https://unsplash.com/license).

## Recursos

- **Visão geral:** modelo, versão do iOS, bateria, armazenamento detalhado, número de série/IMEI/MAC, conexão
  por Wi-Fi, modos Recovery e DFU.
- **Fotos e vídeos:** a biblioteca inteira, lida do banco de dados do app Fotos: álbuns e pastas, favoritos,
  ocultos, apagados recentemente e tipos de mídia. Ordena por data ou tamanho e agrupa por dia, mês ou ano.
- **iCloud Fotos:** entre com seu Apple ID para ver, reproduzir, baixar e apagar o que está só no iCloud.
- **Exportar:** baixa a seleção ou tudo, organizado por mês, com conversão HEIC→JPG e os vídeos das Live
  Photos se quiser.
- **Pré-visualização de vídeos:** os vídeos HEVC do iPhone tocam com imagem, convertidos na hora pelo ffmpeg.
- **Apagar fotos:** o próprio iPhone apaga, como no app Fotos do Windows.
- **Apps:** todos os apps com ícone, versão e tamanho (app e dados). Remover apps e acessar os Documentos dos
  apps que compartilham arquivos.
- **Arquivos:** navega a partição de mídia e os Documentos dos apps. O modo "Todos os arquivos" acha os
  maiores arquivos. Enviar, baixar, criar pasta e apagar.
- **Tela e controle:** espelha a tela do iPhone e controla o aparelho pelo mouse e teclado.
- **Atualizar iOS:** só versões que a Apple ainda assina, **sem apagar os dados**, depois de 11 verificações de
  segurança.
- **Idiomas:** inglês e português, escolhidos no rodapé da barra lateral.

![Fotos](docs/screenshots/photos-pt.png)

Mais imagens no [README em inglês](README.md#features).

## Instalação

1. Baixe o **`iPhoneManager-Setup-<versão>.exe`** em
   [Releases](https://github.com/tmkaio/iphone-manager/releases) e execute. Ele instala só para o seu usuário,
   sem precisar de administrador.
2. Na página **Selecionar componentes** tudo vem marcado. Desmarque o que não quiser:
   - **ffmpeg**: reproduz os vídeos do iPhone dentro do app. O instalador baixa uma versão oficial para Windows
     ([gyan.dev](https://www.gyan.dev/ffmpeg/builds/), cerca de 115 MB) e confere o SHA-256.
   - **Apple Devices** (Microsoft Store): driver e serviço da Apple para falar com o iPhone. O instalador o
     instala pelo `winget`, e o Windows pode pedir permissão. Só aparece quando o driver não está instalado;
     o iTunes e o [iMazing](https://imazing.com) também o trazem.
3. Conecte o iPhone pelo cabo, desbloqueie e toque em **Confiar**.

> O instalador ainda não é assinado, então o Windows SmartScreen pode avisar: clique em **Mais informações ›
> Executar assim mesmo**.

## Como usar

### Fotos e iCloud
Itens com ☁ estão só no iCloud: o iPhone guarda a miniatura, mas não o original. Clique em **☁ Conectar
iCloud** para abrir, baixar ou apagar esses itens. A senha vai direto para a Apple e não é guardada. Não
funciona com a Proteção Avançada de Dados ligada.

**Ao apagar:**
- *Itens no iPhone:* o próprio iPhone apaga. Nos nossos testes, eles não passaram por *Apagados
  recentemente*, então baixe antes o que quiser guardar.
- *Itens só no iCloud:* vão para *Apagados recentemente* em todos os aparelhos e podem ser recuperados por
  30 dias.

### Tela
Na primeira vez, o app baixa e monta a imagem de desenvolvedor da Apple (cerca de 17 MB). Ela não altera nada
no iPhone e some quando ele reinicia. É preciso o iOS 17 ou mais novo, com o **Modo de Desenvolvedor** ligado.

### Atualizar o iOS
É **atualização, não restauração**: dados, apps e fotos são mantidos. Mantenha o cabo conectado até o fim.
Qualquer verificação que falhe bloqueia a atualização.

## Desenvolvimento

```bash
git clone https://github.com/tmkaio/iphone-manager.git
cd iphone-manager
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python run.py
```

### Modo demonstração

```bash
.venv/Scripts/python -m app.demo --fetch
.venv/Scripts/python -m app.demo
```

Abre a interface em http://127.0.0.1:8790 com um iPhone fictício.

## Licença

[GNU GPL v3.0 ou posterior](LICENSE). Projeto independente, sem vínculo com a Apple Inc. ou a DigiDNA
(iMazing). iPhone, iOS e iCloud são marcas da Apple Inc. Use por sua conta e risco e mantenha um backup do
aparelho.
