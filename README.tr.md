> 🚧 **Durum: aktif geliştirme.** Yazarın kendi kullanımında çalışır; arayüzler değişebilir.

# content-package-studio

Elle topladığınız kaynakları (X gönderisi, web sayfası, YouTube transkripti) gözden geçirilmiş bir taslak gönderiye ve doğrulanabilir bir ZIP paketine çeviren yerel, tek kullanıcılı bir web uygulaması. Pakette metin, yanıt, alt metin, kaynak ve hak notları, isteğe bağlı üretilmiş medya ve SHA-256 değerli bir manifest bulunur. Uygulama hiçbir şey yayımlamaz: X'e yazma yolu yoktur, sağlık yanıtı ve her paket manifesti `x_writes_enabled: false` der.

Arayüz, hata mesajları ve editoryal standart Türkçedir; İngilizce ana belge [README.md](README.md) dosyasıdır.

## Neden var

Yapay zekâ ile yazılan taslaklar kanıt izini kolayca kaybeder: hangi kaynak ne dedi, hangi metni insan onayladı, hangi görsel hangi sürüme ait. Uygulama bu izi tek bir SQLite dosyasında tutar:

- kaynak eklenirken dondurulur; bir proje yalnız kendisine açıkça bağlanan kaynakları kullanır;
- her düzenleme yeni bir revizyondur; metin veya medya değişince eski onaylar geçersiz olur, eski medya "bayat" işaretlenir ve pakete girmez;
- model çıktısı yalnızca bir öneridir; aktif metne uygulamak açık bir eylemdir, geç gelen sonuç yazdığınızı ezmez;
- son yerel paket yalnız, tam o önizleme parmak iziyle bağlı bir insan metin onayından sonra üretilir. Sunucu insanın gerçekten baktığını kanıtlayamaz; açık beyanı ve parmak izini kaydeder.

## Hızlı başlangıç

Gereksinim: Python 3.12, Node.js (X karakter sayacı için), isteğe bağlı ffmpeg/ffprobe (video, ses, yükleme).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
npm ci --prefix studio
.venv/Scripts/python -m studio.launcher        # http://127.0.0.1:8767 adresini açar
```

Testler:

```bash
.venv/Scripts/python -X utf8 -m unittest discover -s studio -t . -p "test_*.py"
node --test studio/test_frontend_flows.js
```

Ortam değişkenleri: `HERMES_AGENT_ROOT` (model üretimi için Hermes agent klasörü), `STUDIO_ASR_SSH_HOST` (isteğe bağlı altyazı işi için SSH takma adı, varsayılan `vps`), `STUDIO_REQUIRE_DRIVE` (isteğe bağlı, Windows: veri klasörünü bu sürücüyle sınırlar, ör. `D:`).

## Sınırlar

- **Model üretimi yalnız yazarın kurulumunda çalışır.** Taslak üretimi ve model incelemesi, sabit sağlayıcı/modelle (`openai-codex` / `gpt-5.6-terra`) izole, araçsız bir Hermes agent alt sürecini çağırır ve uygun bir abonelik ister. Bu olmadan kaynak, düzenleme, revizyon, onay, medya ve dışa aktarma çalışır; üretim sahte taslak üretmek yerine hata verir. Gerçek modeli deneyen test `STUDIO_LIVE_MODEL=1` olmadan atlanır.
- Yalnız Windows'ta denendi. CI Windows'ta koşar; Linux ve macOS denenmedi.
- ffmpeg/ffprobe yoksa ilgili testler atlanır; atlama başarı değildir.
- Editoryal standart ve istemler tek bir hesap ve kitle için Türkçe yazılmıştır; tarafsız varsayılan değil, değiştirilecek bir örnek sayın.
- Erişim, etkileşim veya içerik kalitesi hakkında iddia yoktur. Tek kullanıcı, yalnız loopback; bir ağa açmayın.

## Lisans

Kod Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)). Bağımlılıklar kendi lisanslarını korur: [THIRD-PARTY.md](THIRD-PARTY.md).
