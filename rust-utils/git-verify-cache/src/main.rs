// git-verify-cache: a gpg.ssh.program and gpg.program that cache successful
// signature verification results. Git runs the program for each signed
// commit that a format such as %G? shows, so a log of many commits can take
// seconds without a cache.
//
// The name of the executable selects the mode: a name that contains "gpg"
// gives the OpenPGP mode, all other names give the SSH mode. The mode does
// not come from the arguments, so that an unusual signing call can never go
// to the wrong program. In SSH mode, calls other than verification go to
// wlr-git-ssh-sign, which asks for confirmation before it signs. In OpenPGP
// mode, they go to gpg.
//
// SSH results depend only on the inputs (git gives -Overify-time), so they
// do not expire. OpenPGP results also depend on the keyring, the trust
// database and the current time. The key includes file metadata for the
// keyring and trust database. The results expire after a week, or when the
// signature expires if that is earlier. A key that expires during that week
// shows as valid until the result expires. If the gpg configuration names
// keyring or trust database files outside $GNUPGHOME, there is no cache.
//
// Environment:
//   WLR_GIT_VERIFY_CACHE_DIR  cache directory (default: $XDG_CACHE_HOME/wlr-git-verify)
//   WLR_GIT_VERIFY_GPG_TTL    OpenPGP result lifetime in seconds (default: one week)
//   WLR_GIT_VERIFY_LOG        file to append "hit", "miss", "store" and "bypass" events to
//   WLR_GIT_VERIFY_SIGNER     program for SSH calls other than verification
//   WLR_GIT_VERIFY_GPG        gpg program

mod store;

use std::env;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio, exit};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

use store::{Entry, Key, Store};

const CACHE_VERSION: &[u8] = b"wlr-git-verify-cache v2\0";
const SSH_FILE_OPTIONS: [&str; 3] = ["-f", "-s", "-r"];
const SSH_VALUE_OPTIONS: [&str; 3] = ["-n", "-I", "-O"];
const GPG_VERIFY_PREFIX: [&str; 3] = ["--keyid-format=long", "--status-fd=1", "--verify"];
const GPG_DEFAULT_TTL: u64 = 7 * 24 * 60 * 60;
// Options that make gpg read state that gnupg_fingerprint does not see.
const GNUPG_OUTSIDE_FILE_OPTIONS: [&str; 5] = [
    "keyring",
    "primary-keyring",
    "trustdb-name",
    "homedir",
    "no-default-keyring",
];
const GNUPG_CONFIG_FILES: [&str; 2] = ["gpg.conf", "common.conf"];
const GNUPG_STATE_FILES: [&str; 11] = [
    "common.conf",
    "gpg.conf",
    "pubring.kbx",
    "pubring.gpg",
    "trustdb.gpg",
    "tofu.db",
    "public-keys.d/pubring.db",
    "public-keys.d/pubring.db-wal",
    "public-keys.d/pubring.db-journal",
    "trustlist.txt",
    "keyboxd.conf",
];

/// A verification call that the cache can answer.
struct Request {
    mode: &'static str,
    program: OsString,
    key: Key,
    payload: Option<Vec<u8>>,
    expires_at: u64,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs())
}

fn log(event: &str, mode: &str) {
    if let Some(path) = env::var_os("WLR_GIT_VERIFY_LOG")
        && let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path)
    {
        let _ = writeln!(file, "{event} {mode}");
    }
}

fn exec(program: &OsString, args: &[String], mode: &str) -> ! {
    log("bypass", mode);
    let error = Command::new(program).args(args).exec();
    eprintln!("git-verify-cache: cannot run {program:?}: {error}");
    exit(255);
}

fn cache_dir() -> Option<PathBuf> {
    if let Some(dir) = env::var_os("WLR_GIT_VERIFY_CACHE_DIR").filter(|value| !value.is_empty()) {
        return Some(PathBuf::from(dir));
    }
    let base = env::var_os("XDG_CACHE_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".cache")))?;
    Some(base.join("wlr-git-verify"))
}

fn update_field(hasher: &mut Sha256, field: &[u8]) {
    hasher.update((field.len() as u64).to_le_bytes());
    hasher.update(field);
}

fn update_file(hasher: &mut Sha256, path: &str) -> Option<()> {
    let contents = fs::read(path).ok()?;
    update_field(hasher, &Sha256::digest(&contents));
    Some(())
}

fn read_stdin() -> Vec<u8> {
    let mut payload = Vec::new();
    if io::stdin().read_to_end(&mut payload).is_err() {
        exit(255);
    }
    payload
}

/// Hash the SSH arguments in the forms that git uses. Return None for any
/// other form. The caller then sends the call to the signer: a later -Y
/// option overrides the first one, so ssh-keygen could sign.
fn ssh_arguments(hasher: &mut Sha256, args: &[String]) -> Option<()> {
    let mut index = 2;
    while index < args.len() {
        let option = args[index].as_str();
        if SSH_FILE_OPTIONS.contains(&option) {
            update_field(hasher, option.as_bytes());
            update_file(hasher, args.get(index + 1)?)?;
            index += 2;
        } else if SSH_VALUE_OPTIONS.contains(&option) {
            update_field(hasher, option.as_bytes());
            update_field(hasher, args.get(index + 1)?.as_bytes());
            index += 2;
        } else if option.starts_with("-Overify-time=") {
            update_field(hasher, option.as_bytes());
            index += 1;
        } else {
            return None;
        }
    }
    Some(())
}

fn ssh_request(args: &[String]) -> Request {
    let keygen: OsString = "ssh-keygen".into();
    let operation = match args {
        [flag, operation, ..] if flag == "-Y" => operation.as_str(),
        _ => exec(&signer(), args, "ssh"),
    };
    let reads_stdin = match operation {
        "verify" | "check-novalidate" => true,
        "find-principals" => false,
        _ => exec(&signer(), args, "ssh"),
    };
    let mut hasher = Sha256::new();
    hasher.update(CACHE_VERSION);
    update_field(&mut hasher, b"ssh");
    update_field(&mut hasher, operation.as_bytes());
    if ssh_arguments(&mut hasher, args).is_none() {
        exec(&signer(), args, "ssh");
    }
    let payload = reads_stdin.then(read_stdin);
    if let Some(payload) = &payload {
        update_field(&mut hasher, payload);
    }
    Request {
        mode: "ssh",
        program: keygen,
        key: hasher.finalize().into(),
        payload,
        expires_at: u64::MAX,
    }
}

fn signer() -> OsString {
    env::var_os("WLR_GIT_VERIFY_SIGNER").unwrap_or_else(|| "wlr-git-ssh-sign".into())
}

fn gnupg_home() -> Option<PathBuf> {
    env::var_os("GNUPGHOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".gnupg")))
}

/// Hash the metadata of the files that can change an OpenPGP verification
/// result. Metadata is much faster than file contents for a large keyring,
/// and ctime changes on each write.
fn gnupg_fingerprint(hasher: &mut Sha256, home: &Path) {
    update_field(hasher, home.as_os_str().as_encoded_bytes());
    for name in GNUPG_STATE_FILES {
        update_field(hasher, name.as_bytes());
        match fs::metadata(home.join(name)) {
            Ok(metadata) => {
                for value in [
                    metadata.dev(),
                    metadata.ino(),
                    metadata.size(),
                    metadata.mtime() as u64,
                    metadata.mtime_nsec() as u64,
                    metadata.ctime() as u64,
                    metadata.ctime_nsec() as u64,
                ] {
                    hasher.update(value.to_le_bytes());
                }
            }
            Err(_) => hasher.update(b"missing"),
        }
    }
}

/// Return true if a gpg configuration file sets an option that makes gpg
/// read keyring or trust database files outside `home`.
fn gnupg_uses_outside_files(home: &Path) -> bool {
    GNUPG_CONFIG_FILES.iter().any(|name| {
        fs::read_to_string(home.join(name)).is_ok_and(|config| {
            config.lines().any(|line| {
                let line = line.trim_start();
                let option = line.strip_prefix("--").unwrap_or(line);
                let name = option.split_whitespace().next().unwrap_or_default();
                GNUPG_OUTSIDE_FILE_OPTIONS.contains(&name)
            })
        })
    })
}

/// Return the earliest signature expiry time in the gpg status output, as
/// seconds since the epoch. Return `Some(u64::MAX)` if no signature expires,
/// and None if an expiry time is not in seconds.
fn signature_expiry(status: &[u8]) -> Option<u64> {
    let mut earliest = u64::MAX;
    for line in String::from_utf8_lossy(status).lines() {
        let mut fields = line.split_whitespace();
        if fields.next() != Some("[GNUPG:]") || fields.next() != Some("VALIDSIG") {
            continue;
        }
        // VALIDSIG <fingerprint> <date> <timestamp> <expiry timestamp> ...
        let expiry: u64 = fields.nth(3)?.parse().ok()?;
        if expiry != 0 {
            earliest = earliest.min(expiry);
        }
    }
    Some(earliest)
}

fn gpg_request(args: &[String]) -> Request {
    let gpg = env::var_os("WLR_GIT_VERIFY_GPG").unwrap_or_else(|| "gpg".into());
    let verify = args.len() == 5 && args[..3] == GPG_VERIFY_PREFIX && args[4] == "-";
    let Some(home) = gnupg_home().filter(|home| verify && !gnupg_uses_outside_files(home)) else {
        exec(&gpg, args, "gpg");
    };
    let mut hasher = Sha256::new();
    hasher.update(CACHE_VERSION);
    update_field(&mut hasher, b"gpg");
    for arg in &args[..3] {
        update_field(&mut hasher, arg.as_bytes());
    }
    if update_file(&mut hasher, &args[3]).is_none() {
        exec(&gpg, args, "gpg");
    }
    gnupg_fingerprint(&mut hasher, &home);
    let payload = read_stdin();
    update_field(&mut hasher, &payload);
    let ttl = env::var("WLR_GIT_VERIFY_GPG_TTL")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(GPG_DEFAULT_TTL);
    Request {
        mode: "gpg",
        program: gpg,
        key: hasher.finalize().into(),
        payload: Some(payload),
        expires_at: now().saturating_add(ttl),
    }
}

fn main() {
    let mut argv = env::args();
    let name = argv
        .next()
        .map(|arg0| {
            Path::new(&arg0)
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned()
        })
        .unwrap_or_default();
    let args: Vec<String> = argv.collect();
    let request = if name.contains("gpg") {
        gpg_request(&args)
    } else {
        ssh_request(&args)
    };

    let time = now();
    let store = cache_dir().map(Store::new);
    if let Some(entry) = store
        .as_ref()
        .and_then(|store| store.get(&request.key, time))
    {
        log("hit", request.mode);
        let _ = io::stdout().write_all(&entry.stdout);
        let _ = io::stderr().write_all(&entry.stderr);
        exit(0);
    }
    log("miss", request.mode);

    let mut child = Command::new(&request.program)
        .args(&args)
        .stdin(if request.payload.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap_or_else(|error| {
            eprintln!(
                "git-verify-cache: cannot run {:?}: {error}",
                request.program
            );
            exit(255);
        });
    let writer = child
        .stdin
        .take()
        .zip(request.payload)
        .map(|(mut stdin, payload)| {
            thread::spawn(move || {
                let _ = stdin.write_all(&payload);
            })
        });
    let output = child.wait_with_output().unwrap_or_else(|_| exit(255));
    if let Some(writer) = writer {
        let _ = writer.join();
    }
    let _ = io::stdout().write_all(&output.stdout);
    let _ = io::stderr().write_all(&output.stderr);

    let expires_at = match request.mode {
        "gpg" => signature_expiry(&output.stdout).map(|expiry| expiry.min(request.expires_at)),
        _ => Some(request.expires_at),
    };
    if output.status.success()
        && let Some(store) = &store
        && let Some(expires_at) = expires_at.filter(|&expiry| expiry > time)
    {
        let entry = Entry {
            key: request.key,
            expires_at,
            stdout: output.stdout,
            stderr: output.stderr,
        };
        if store.put(&entry, time).is_ok() {
            log("store", request.mode);
        }
    }
    exit(output.status.code().unwrap_or(255));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fingerprint(home: &Path) -> [u8; 32] {
        let mut hasher = Sha256::new();
        gnupg_fingerprint(&mut hasher, home);
        hasher.finalize().into()
    }

    #[test]
    fn fingerprint_tracks_keyring_and_trust_changes() {
        let home = tempfile::tempdir().unwrap();
        fs::create_dir(home.path().join("public-keys.d")).unwrap();
        let empty = fingerprint(home.path());
        fs::write(home.path().join("public-keys.d/pubring.db"), b"keys").unwrap();
        let with_keys = fingerprint(home.path());
        assert_ne!(empty, with_keys);
        assert_eq!(with_keys, fingerprint(home.path()));
        fs::write(home.path().join("trustdb.gpg"), b"trust").unwrap();
        let with_trust = fingerprint(home.path());
        assert_ne!(with_keys, with_trust);
        // Same size, new contents: ctime and mtime change.
        std::thread::sleep(std::time::Duration::from_millis(10));
        fs::write(home.path().join("trustdb.gpg"), b"TRUST").unwrap();
        assert_ne!(with_trust, fingerprint(home.path()));
        // Ignore files that do not change verification results.
        fs::write(home.path().join("random_seed"), b"seed").unwrap();
        fs::write(home.path().join("gpg-agent.conf"), b"").unwrap();
        let before = fingerprint(home.path());
        fs::write(home.path().join("random_seed"), b"new seed").unwrap();
        assert_eq!(before, fingerprint(home.path()));
    }

    #[test]
    fn outside_file_options_disable_the_cache() {
        let home = tempfile::tempdir().unwrap();
        assert!(!gnupg_uses_outside_files(home.path()));
        fs::write(home.path().join("common.conf"), "use-keyboxd\n").unwrap();
        fs::write(
            home.path().join("gpg.conf"),
            "# keyring x\ntrust-model pgp\n",
        )
        .unwrap();
        assert!(!gnupg_uses_outside_files(home.path()));
        for config in [
            "keyring /other/pubring.kbx",
            "  --trustdb-name /x",
            "homedir /y",
        ] {
            fs::write(home.path().join("gpg.conf"), format!("armor\n{config}\n")).unwrap();
            assert!(gnupg_uses_outside_files(home.path()), "{config}");
        }
    }

    #[test]
    fn signature_expiry_uses_the_earliest_validsig_expiry() {
        let line = |expiry: &str| {
            format!("[GNUPG:] VALIDSIG 61FC 2024-02-11 1707675526 {expiry} 4 0 22 10 00 C588\n")
        };
        assert_eq!(signature_expiry(b"[GNUPG:] GOODSIG X\n"), Some(u64::MAX));
        assert_eq!(signature_expiry(line("0").as_bytes()), Some(u64::MAX));
        let both = line("1800000000") + &line("1750000000");
        assert_eq!(signature_expiry(both.as_bytes()), Some(1_750_000_000));
        assert_eq!(signature_expiry(line("20270101T000000").as_bytes()), None);
        assert_eq!(signature_expiry(b"[GNUPG:] VALIDSIG short\n"), None);
    }

    #[test]
    fn ssh_arguments_hash_file_contents_not_paths() {
        let dir = tempfile::tempdir().unwrap();
        let first = dir.path().join("first");
        let second = dir.path().join("second");
        fs::write(&first, b"signature").unwrap();
        fs::write(&second, b"signature").unwrap();
        let hash = |path: &Path| {
            let args: Vec<String> = ["-Y", "verify", "-n", "git", "-s", path.to_str().unwrap()]
                .map(String::from)
                .to_vec();
            let mut hasher = Sha256::new();
            ssh_arguments(&mut hasher, &args).unwrap();
            <[u8; 32]>::from(hasher.finalize())
        };
        assert_eq!(hash(&first), hash(&second));
        fs::write(&second, b"other").unwrap();
        assert_ne!(hash(&first), hash(&second));
    }

    #[test]
    fn ssh_arguments_reject_unknown_forms() {
        for args in [
            vec!["-Y", "verify", "-sfile"],
            vec!["-Y", "verify", "-Y", "sign"],
            vec!["-Y", "verify", "-Oprint-pubkey"],
            vec!["-Y", "verify", "-n"],
        ] {
            let args: Vec<String> = args.into_iter().map(String::from).collect();
            assert!(
                ssh_arguments(&mut Sha256::new(), &args).is_none(),
                "{args:?}"
            );
        }
    }
}
