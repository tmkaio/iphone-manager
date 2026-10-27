"""Portuguese (Brazil) translations of the server's messages: English text -> Portuguese."""

PT: dict[str, str] = {
    # server.py
    "Device not found: {error}":
        "Dispositivo não encontrado: {error}",
    "An update is in progress":
        "Há uma atualização em andamento",
    "Connect the iPhone with the USB cable":
        "Conecte o iPhone pelo cabo USB",
    "Couldn't enter Recovery: {error}":
        "Não foi possível entrar em Recovery: {error}",
    "Couldn't read the photo library: {error}":
        "Não foi possível ler a biblioteca de fotos: {error}",
    "ffmpeg not found":
        "ffmpeg não encontrado",
    "Couldn't read the video: {error}":
        "Não foi possível ler o vídeo: {error}",
    "ffmpeg not found in PATH":
        "ffmpeg não encontrado no PATH",
    "Nothing selected":
        "Nada selecionado",
    "Confirmation required":
        "Confirmação necessária",
    "To delete photos, connect the iPhone with the USB cable":
        "Para apagar fotos, conecte o iPhone pelo cabo USB",
    "Unknown item: {path} (reload the photos)":
        "Item desconhecido: {path} (recarregue as fotos)",
    "Delete failed: {error}":
        "Falha ao apagar: {error}",
    "Couldn't remove the app: {error}":
        "Não foi possível remover o app: {error}",
    "Couldn't mount the developer disk image: {error}":
        "Não foi possível montar a imagem de desenvolvedor: {error}",
    "Couldn't start mirroring: {error}":
        "Não foi possível iniciar o espelhamento: {error}",
    "Couldn't query ipsw.me: {error}":
        "Não foi possível consultar ipsw.me: {error}",
    "Invalid IPSW: {error}":
        "IPSW inválido: {error}",
    "Only IPSW files from Apple's servers are accepted":
        "Só são aceitos IPSW dos servidores da Apple",
    "Update blocked — {reasons}":
        "Atualização bloqueada — {reasons}",
    "Choose the destination folder":
        "Escolha a pasta de destino",
    "Choose the IPSW file":
        "Escolha o arquivo IPSW",
    "iOS firmware":
        "Firmware iOS",
    "All files":
        "Todos",
    # device.py
    "The Apple Mobile Device service is still starting, wait a few seconds.":
        "O serviço Apple Mobile Device ainda está iniciando, aguarde alguns segundos.",
    "the iPhone didn't answer; unlock it or reconnect the cable":
        "o iPhone não respondeu; desbloqueie-o ou reconecte o cabo",
    "Unlock the iPhone and tap 'Trust This Computer'.":
        "Desbloqueie o iPhone e toque em 'Confiar neste computador'.",
    # files.py
    "unknown root: {root}":
        "raiz desconhecida: {root}",
    "This app doesn't share files with the computer (only apps with 'File Sharing' give access to their Documents). ({error})":
        "Este app não compartilha arquivos com o computador (só apps com 'Compartilhamento de arquivos' permitem acesso aos Documentos). ({error})",
    "invalid file name":
        "nome de arquivo inválido",
    "A file named {name} already exists in this folder":
        "Já existe um arquivo chamado {name} nesta pasta",
    "Photo library files (DCIM/PhotoData) can't be deleted here: the Photos app would be left with broken items. Delete them on the iPhone itself.":
        "Arquivos da biblioteca de fotos (DCIM/PhotoData) não podem ser apagados por aqui: o app Fotos ficaria com itens quebrados. Apague-os pelo próprio iPhone.",
    # firmware.py
    "File not found":
        "Arquivo não encontrado",
    "SHA1 mismatch — the file is corrupted, download it again":
        "SHA1 não confere — arquivo corrompido, baixe novamente",
    "An update is already in progress":
        "Já existe uma atualização em andamento",
    "ERROR: {error}":
        "ERRO: {error}",
    # icloud.py
    "session expired":
        "sessão expirada",
    "Sign in first":
        "Faça o login primeiro",
    "Incorrect code":
        "Código incorreto",
    "Connect iCloud first":
        "Conecte o iCloud primeiro",
    "This item wasn't found in iCloud":
        "Este item não foi encontrado no iCloud",
    "iCloud didn't report this item's original":
        "O iCloud não informou o original deste item",
    "iCloud didn't offer this item's original":
        "O iCloud não ofereceu o original deste item",
    "File too large to preview":
        "Arquivo grande demais para pré-visualizar",
    "iCloud refused the deletion":
        "O iCloud recusou a exclusão",
    "Incorrect Apple ID or password":
        "Apple ID ou senha incorretos",
    "Account locked by Apple":
        "Conta bloqueada pela Apple",
    "iCloud doesn't allow web access (is Advanced Data Protection on?)":
        "O iCloud não permite acesso pela web (Proteção Avançada de Dados ligada?)",
    # modes.py
    "MobileDevice.dll not found (install iMazing or the Apple Devices app)":
        "MobileDevice.dll não encontrado (instale o iMazing ou o app Apple Devices)",
    "MobileDevice: couldn't register for device notifications":
        "MobileDevice: falha ao registrar notificações de dispositivos",
    "MobileDevice is not running":
        "MobileDevice não está ativo",
    "The device isn't in the expected mode (it is in {state})":
        "O aparelho não está no modo esperado (está em {state})",
    "The device can't be reached over USB":
        "O aparelho não está acessível por USB",
    "{call} failed ({code})":
        "{call} falhou ({code})",
    "The device is no longer in Recovery mode":
        "O aparelho não está mais em modo Recovery",
    # preflight.py
    "iPhone connected with a USB cable":
        "iPhone conectado por cabo USB",
    "The update doesn't work over Wi-Fi":
        "A atualização não funciona por Wi-Fi",
    "IPSW file readable":
        "Arquivo IPSW legível",
    "IPSW made for this model ({product_type} / {hardware_model})":
        "IPSW feito para este modelo ({product_type} / {hardware_model})",
    "The IPSW is for: {products}":
        "O IPSW é para: {products}",
    "IPSW has the update mode (keeps the data)":
        "IPSW tem o modo atualização (mantém os dados)",
    "This IPSW only allows a restore that erases everything":
        "Este IPSW só permite restaurar apagando tudo",
    "Version {version} same as or newer than the current one ({current})":
        "Versão {version} igual ou mais nova que a atual ({current})",
    "Going back to an earlier version requires erasing the iPhone":
        "Voltar para uma versão anterior exige apagar o iPhone",
    "Apple still signs iOS {version} ({build})":
        "Apple ainda assina o iOS {version} ({build})",
    "Couldn't check with Apple/ipsw.me":
        "Não foi possível consultar a Apple/ipsw.me",
    "Apple no longer signs this version: the install would fail":
        "A Apple não assina mais esta versão: a instalação falharia",
    "IPSW file complete (size matches Apple's)":
        "Arquivo IPSW completo (tamanho confere com a Apple)",
    "It has {size} bytes, it should have {expected}: download it again":
        "Tem {size} bytes, deveria ter {expected}: baixe de novo",
    "IPSW intact (archive index readable)":
        "IPSW íntegro (índice do arquivo legível)",
    "Communication with the iPhone":
        "Comunicação com o iPhone",
    "It is the same iPhone that was selected (ECID matches)":
        "É o mesmo iPhone selecionado (ECID confere)",
    "Enough battery":
        "Bateria suficiente",
    "The iPhone didn't report its battery: unlock it and check again":
        "O iPhone não informou a bateria: desbloqueie-o e verifique de novo",
    "Enough battery ({level}%, charging)":
        "Bateria suficiente ({level}%, carregando)",
    "Enough battery ({level}%)":
        "Bateria suficiente ({level}%)",
    "Needs at least {need}% while charging":
        "Precisa de pelo menos {need}% carregando",
    "Needs at least {need}%":
        "Precisa de pelo menos {need}%",
    "Free space on the iPhone":
        "Espaço livre no iPhone",
    "The iPhone didn't report its free space: unlock it and check again":
        "O iPhone não informou o espaço livre: desbloqueie-o e verifique de novo",
    "Free space on the iPhone ({gb} GB)":
        "Espaço livre no iPhone ({gb} GB)",
    "Free up at least {gb} GB":
        "Libere pelo menos {gb} GB",
    "Free space on the computer ({gb} GB)":
        "Espaço livre no computador ({gb} GB)",
    "Free up at least {gb} GB on the Windows disk":
        "Libere pelo menos {gb} GB no disco do Windows",
    # ptp.py
    "Windows doesn't see the iPhone as a camera. Unlock it and confirm that it trusts this computer.":
        "O Windows não está vendo o iPhone como câmera. Desbloqueie-o e confirme que confia neste computador.",
    "More than one iPhone is connected: leave only the one you want to change.":
        "Há mais de um iPhone conectado: deixe só o que quer alterar.",
    "The iPhone didn't show its photos to Windows: unlock it and try again.":
        "O iPhone não mostrou as fotos ao Windows: desbloqueie-o e tente de novo.",
    "Windows refused the deletion ({error}). Nothing or only part was deleted: check on the iPhone.":
        "O Windows recusou a exclusão ({error}). Nada foi apagado ou só parte: confira no iPhone.",
    "Windows interrupted the deletion: check on the iPhone which photos were deleted.":
        "O Windows interrompeu a exclusão: confira no iPhone quais fotos foram apagadas.",
    # screen.py
    "Mounting took too long and was stopped":
        "A montagem demorou demais e foi interrompida",
    "code {code}":
        "código {code}",
    "Developer disk image not mounted":
        "Imagem de desenvolvedor não montada",
    "mirroring stopped while starting: {output}":
        "o espelhamento parou ao iniciar: {output}",
    # sqlite_remote.py
    "not a SQLite database":
        "não é um banco SQLite",
    "page {n} incomplete":
        "página {n} incompleta",
    "page {n}: unexpected type {kind}":
        "página {n}: tipo inesperado {kind}",
    "invalid column type {type}":
        "tipo de coluna inválido {type}",
    # video.py
    "ffmpeg couldn't convert this video":
        "ffmpeg não conseguiu converter este vídeo",
    # mobiledevice.py
    "Apple Mobile Device service not found. Install iMazing or the Apple Devices app (Microsoft Store).":
        "Serviço Apple Mobile Device não encontrado. Instale o iMazing ou o app Apple Devices (Microsoft Store).",
    # irecv_md.py
    "The iPhone didn't show up in {mode} mode within {seconds} s":
        "O iPhone não apareceu em modo {mode} em {seconds} s",
    "command '{cmd}' failed ({code})":
        "comando '{cmd}' falhou ({code})",
    "file":
        "arquivo",
    "sending {name} failed ({code})":
        "envio de {name} falhou ({code})",
    "{name}: 100% ({size} bytes sent)":
        "{name}: 100% ({size} bytes enviados)",
    "MobileDevice.dll unavailable ({error}); using libusb":
        "MobileDevice.dll indisponível ({error}); usando libusb",
    "Recovery mode: USB communication through MobileDevice.dll (Apple's driver)":
        "Modo Recovery: comunicação USB pelo MobileDevice.dll (driver da Apple)",
}
