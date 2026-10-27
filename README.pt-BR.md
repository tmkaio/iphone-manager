# iPhone Manager

**Um gerenciador de iPhone gratuito e de código aberto para Windows, macOS e Linux.** Veja e exporte suas fotos (inclusive o
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
- **Apagar fotos:** o próprio iPhone apaga, como no app Fotos do Windows (por enquanto só no Windows).
- **Apps:** todos os apps com ícone, versão e tamanho (app e dados). Remover apps e acessar os Documentos dos
  apps que compartilham arquivos.
- **Arquivos:** navega a partição de mídia e os Documentos dos apps. O modo "Todos os arquivos" acha os
  maiores arquivos. Enviar, baixar, criar pasta e apagar.
- **Tela e controle:** espelha a tela do iPhone e controla o aparelho pelo mouse e teclado.
- **Backups:** backup completo do iPhone no formato do iTunes/Finder (incremental, com criptografia opcional), para restaurar depois de um recovery ou de uma atualização que falhou. Backup e restauração **dos dados de um app**, das **Mensagens**, dos **Contatos**, do histórico de chamadas, Notas, Calendário ou favoritos do Safari separadamente, sem mexer no resto.
- **Atualizar iOS:** só versões que a Apple ainda assina, **sem apagar os dados**, depois de 11 verificações de
  segurança.
- **Idiomas:** inglês e português, escolhidos no rodapé da barra lateral.

![Fotos](docs/screenshots/photos-pt.png)

Mais imagens no [README em inglês](README.md#features).

## Instalação

### Windows

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

### macOS (novo, beta)

1. Baixe o **`iPhoneManager-<versão>-macOS-arm64.dmg`** em
   [Releases](https://github.com/tmkaio/iphone-manager/releases). Funciona em Macs com chip Apple (M1 em
   diante); Macs com Intel não são suportados.
2. Abra e arraste o **iPhone Manager** para **Aplicativos**.
3. Abra pelo Aplicativos, conecte o iPhone pelo cabo, desbloqueie e toque em **Confiar**.

O macOS já tem o serviço da Apple para o iPhone, então não há mais nada para instalar. O app não fica no Dock:
a janela abre no Google Chrome, Microsoft Edge, Brave ou Chromium (modo app), se algum estiver instalado, ou no
navegador padrão. Abrir o app de novo mostra a janela de novo. Para ver vídeos, instale o ffmpeg pelo
[Homebrew](https://brew.sh): `brew install ffmpeg`.

### Linux (novo, beta)

1. Instale o usbmuxd (serviço do iPhone) da sua distribuição, por exemplo `sudo apt install usbmuxd`.
2. Baixe o **`iPhoneManager-<versão>-x86_64.AppImage`** (ou `aarch64`) em
   [Releases](https://github.com/tmkaio/iphone-manager/releases), torne-o executável e rode:
   `chmod +x iPhoneManager-*.AppImage && ./iPhoneManager-*.AppImage`
3. Conecte o iPhone pelo cabo, desbloqueie e toque em **Confiar**.

Para ver vídeos, instale o ffmpeg (`sudo apt install ffmpeg`). O modo Recovery e a atualização do iOS falam
com o iPhone direto pela USB; se o seu usuário não tiver acesso, crie uma regra udev como
`SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", MODE="0666"` em `/etc/udev/rules.d/39-iphone.rules`.

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

### Backups
**iPhone inteiro** faz um backup completo, igual ao iTunes ou Finder: o primeiro copia tudo (pode demorar), os
próximos só o que mudou. Restaure depois de um recovery, de uma atualização que deu errado ou num iPhone novo.
Ligue **Criptografar backups** para guardar também senhas, Saúde, histórico de chamadas e redes Wi-Fi; sem essa
senha o backup criptografado não pode ser restaurado.

**Só alguns dados** e o botão **Fazer backup** ao lado de cada app salvam só aquela parte: Mensagens (com
anexos), Contatos, histórico de chamadas, Notas, Calendário, favoritos do Safari ou os dados de um app. O iOS só
restaura backups inteiros, então **Restaurar…** faz como o iMazing: faz um backup do iPhone como está agora,
coloca a parte salva no lugar da atual e restaura esse backup mesclado. Todo o resto volta exatamente como
estava um minuto antes.

Antes de restaurar, desligue o **Buscar** no iPhone e mantenha o cabo conectado; o iPhone reinicia no final.

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
