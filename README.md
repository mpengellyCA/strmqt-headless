# strmqt-headless

Run a [StrmQt](https://github.com/mpengellyCA/strmQt-linux) streaming host with nobody
at its screen — on a mini PC in a cupboard, or in a Docker container — and look after it
from your own computer with one tool, `strmqtctl`.

A streaming host is StrmQt on a screen of its own, streamed by Sunshine to
[StrmLight](https://github.com/mpengellyCA/strmLight-android) on a Fire TV or Android TV.
StrmQt sets one up from its Settings; this is for when there is no desktop to open
Settings on.

```
strmqtctl target add den --ssh me@mini-pc --method docker
strmqtctl install
strmqtctl pair 4831                  # the code StrmLight shows on the TV
strmqtctl youtube-music import       # signs the host in with your Firefox's sign-in
```

## How it fits together

`strmqtctl` runs on **your** computer. It knows *targets*: a machine — this one, or one
it reaches with `ssh` — and how StrmQt is installed there:

| Method        | What runs on the target                                                     |
|---------------|-----------------------------------------------------------------------------|
| **docker**    | The `strmqt-headless` image: KWin, PipeWire, Sunshine and StrmQt in one container. |
| **baremetal** | StrmQt and Sunshine from the distribution's packages, as systemd user services. |

That choice is the only difference. Every command after `install` is the same for every
target: it runs StrmQt's own `strmqt admin` there — inside the container through
`docker compose exec`, or as it is on bare metal. You never need to run `docker compose
exec` yourself, but you can (see [By hand](#by-hand)).

```
 your computer                          target (over SSH, or this computer)
┌───────────────────────┐   ssh    ┌──────────────────────────────────────────┐
│ strmqtctl             │ ───────▶ │ docker compose exec … strmqt admin pair  │
│  Firefox's sign-in ───┼──stdin──▶│   or: strmqt admin pair                  │
└───────────────────────┘          │ KWin ─ StrmQt ─ Sunshine ──▶ StrmLight   │
                                   └──────────────────────────────────────────┘
```

## Install strmqtctl

Python 3.10 or newer and the system's `ssh`; nothing else.

```
pipx install git+https://github.com/mpengellyCA/strmqt-headless
```

or run it from a checkout: `./strmqtctl --help`.

## Targets

```
strmqtctl target add NAME --method docker|baremetal [--ssh USER@HOST] [--port N]
                          [--identity KEY] [--copy-id] [--directory DIR] [--default]
strmqtctl target list
strmqtctl target default NAME
strmqtctl target remove NAME          # forgets it here; changes nothing on it
```

Without `--ssh` the target is this computer. With it, `strmqtctl` uses your own `ssh`:
your keys, agent, `~/.ssh/config` and known hosts all apply, a password or passphrase is
asked on the terminal, and one connection is shared for two minutes so a command with
several steps asks once. `--copy-id` installs your key there first (`ssh-copy-id`), after
which nothing is asked at all. Host keys are checked as `ssh` always checks them.

`--directory` (Docker) is where `compose.yaml` and `.env` go on the target, by default
`~/strmqt-headless`. Use `-t NAME` on any command to act on a target other than the
default.

## Installing a host

```
strmqtctl install            # Docker: writes compose.yaml and .env, pulls, starts
strmqtctl status
```

**Docker** needs Docker with its compose plugin on the target, and a GPU: `/dev/dri` is
passed through for KWin's screen and Sunshine's hardware encoder. `install` finds the
groups that own the GPU there and writes them to `.env`. The container uses the
machine's own network (StrmLight finds the host by a broadcast; the stream is UDP), and
StrmQt's ports 8330–8356 on it. Everything the host keeps — pairings, the Emby sign-in,
YouTube Music's cookies, Sunshine's certificate — is in the `strmqt-home` volume.

**Bare metal** needs StrmQt 0.9.2 or newer, Sunshine (a 2026 release), KWin and
PipeWire. `install` says what is missing in your distribution's words, then sets the host
up exactly as Settings → Streaming host would. For it to start at boot rather than at
log-in, the user needs lingering: `sudo loginctl enable-linger USER` on the target.

A machine runs one or the other: `install` refuses a Docker host where a bare-metal one
already runs, since both want the same ports.

## Day to day

```
strmqtctl status [--json]          # running? ports, paired devices, stream, Emby, YouTube Music
strmqtctl pair CODE                # the four digits StrmLight shows; pairs its stream and remote
strmqtctl devices [--json]         # paired StrmLights and phones
strmqtctl unpair ID | --all
strmqtctl stream [--fps 30|60] [--bitrate KBIT/S|auto] [--codec auto|hevc|av1|h264]
                 [--audio 2|51|71] [--height 720|1080|1440|2160]
strmqtctl logs [-f] [-n LINES]
strmqtctl start | stop | restart
strmqtctl update                   # Docker: the newest image; bare metal: restarts on what is installed
strmqtctl uninstall [--purge]      # --purge (Docker) also deletes the volume
strmqtctl shell                    # a shell there (Docker: in the container)
```

StrmLight reads the stream settings before each stream, so `fps`, `bitrate`, `codec` and
`audio` apply to the next one; `height` is the screen's size and restarts the host.

Signing in to Emby is done on the TV, with the remote, as on any StrmQt.

## YouTube Music

Google does not let StrmQt's built-in browser sign in, so StrmQt borrows the sign-in of
a Firefox that is signed in. A host has no Firefox, so `strmqtctl` reads yours:

```
strmqtctl youtube-music import [--list] [--source N|NAME]
strmqtctl youtube-music sign-out
```

It looks at the Firefox profiles on your computer (each container tab group is a choice
of its own), copies the cookie store so Firefox is not disturbed, and sends **only** the
youtube.com and google.com cookies of the one you choose, over SSH, to the host's
StrmQt, which takes them into its YouTube Music view. Nothing else from Firefox leaves
your computer, and nothing is written to Firefox. Sign the host out with `sign-out`, or
by signing that Firefox profile out of Google.

YouTube Music must be switched on in the host's StrmQt (Settings → YouTube).

## By hand

The Docker method is an ordinary compose project; [compose.yaml](compose.yaml) is the
file `install` writes.

```
docker compose up -d
docker compose exec strmqt strmqt admin status
docker compose exec strmqt strmqt admin pair 4831
docker compose exec strmqt strmqt admin help
```

On bare metal the same commands are `strmqt admin …`.

## The image

`ghcr.io/mpengellyca/strmqt-headless` is Ubuntu 26.04 with StrmQt's and Sunshine's own
release packages, each checked against the SHA-256 GitHub records for it. Inside, a
D-Bus session bus and a PipeWire stand in for a desktop session, and StrmQt runs its
host in the foreground (`strmqt admin host run`): the same three services it writes for
systemd anywhere else, started and restarted by StrmQt itself.

Some choices worth knowing:

- **No keyring.** A keyring would ask for its password on the TV, where nobody can type
  it, so StrmQt keeps its Emby sign-in in its owner-only vault file in the volume.
- **Qt WebEngine's sandbox is off** (`QTWEBENGINE_DISABLE_SANDBOX=1`): Chromium's sandbox
  needs user namespaces, which Docker's default seccomp profile refuses. The container
  is the boundary instead.
- **Health.** `docker ps` shows the host healthy once its StrmQt answers
  `strmqt admin status`.

To build it yourself, with a StrmQt package of your own if you like:

```
cp strmqt_*_amd64.deb docker/packages/     # optional: otherwise the release's is fetched
docker build -t ghcr.io/mpengellyca/strmqt-headless:dev docker/
strmqtctl install --tag dev --no-pull
```

## Development

```
python3 -m unittest discover -s tests
```

The tests use scratch homes and fake `strmqt` and `docker` programs; no host, SSH server
or Firefox profile of yours is touched.

## License

GPL-3.0-or-later, as StrmQt.
