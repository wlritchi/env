use std::fs;
use std::io::Write;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use tempfile::TempDir;

/// A test home with the binary under both names and fake programs that
/// record each call.
struct Fixture {
    dir: TempDir,
}

const FAKE: &str = r#"#!/bin/sh
printf '%s %s\n' "${0##*/}" "$*" >> "$FAKE_CALLS"
cat > /dev/null
printf '%s\n' "${FAKE_STDOUT:-out from ${0##*/}}"
printf 'err from %s\n' "${0##*/}" >&2
exit "${FAKE_STATUS:-0}"
"#;

impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let bin = dir.path().join("bin");
        fs::create_dir(&bin).unwrap();
        let binary = env!("CARGO_BIN_EXE_git-verify-cache");
        symlink(binary, bin.join("wlr-git-ssh-verify")).unwrap();
        symlink(binary, bin.join("wlr-git-gpg-verify")).unwrap();
        for name in ["ssh-keygen", "gpg", "wlr-git-ssh-sign"] {
            let path = bin.join(name);
            fs::write(&path, FAKE).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        }
        fs::create_dir(dir.path().join("gnupg")).unwrap();
        fs::write(dir.path().join("calls"), b"").unwrap();
        Self { dir }
    }

    fn path(&self, name: &str) -> PathBuf {
        self.dir.path().join(name)
    }

    fn write(&self, name: &str, contents: &[u8]) -> String {
        let path = self.path(name);
        fs::write(&path, contents).unwrap();
        path.to_str().unwrap().to_owned()
    }

    fn calls(&self) -> Vec<String> {
        fs::read_to_string(self.path("calls"))
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect()
    }

    fn run(&self, name: &str, args: &[&str], stdin: &[u8], envs: &[(&str, &str)]) -> Output {
        let path = format!(
            "{}:{}",
            self.path("bin").display(),
            std::env::var("PATH").unwrap_or_default()
        );
        let mut child = Command::new(self.path("bin").join(name))
            .args(args)
            .env("PATH", path)
            .env("FAKE_CALLS", self.path("calls"))
            .env("WLR_GIT_VERIFY_CACHE_DIR", self.path("cache"))
            .env("GNUPGHOME", self.path("gnupg"))
            .envs(envs.iter().copied())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child.stdin.take().unwrap().write_all(stdin).unwrap();
        child.wait_with_output().unwrap()
    }

    fn ssh(&self, args: &[&str], stdin: &[u8]) -> Output {
        self.run("wlr-git-ssh-verify", args, stdin, &[])
    }

    fn gpg(&self, signature: &str, stdin: &[u8], envs: &[(&str, &str)]) -> Output {
        let args = [
            "--keyid-format=long",
            "--status-fd=1",
            "--verify",
            signature,
            "-",
        ];
        self.run("wlr-git-gpg-verify", &args, stdin, envs)
    }
}

fn verify_args<'a>(signature: &'a str, allowed: &'a str) -> Vec<&'a str> {
    vec![
        "-Y",
        "verify",
        "-n",
        "git",
        "-f",
        allowed,
        "-I",
        "user@example.com",
        "-s",
        signature,
        "-Overify-time=20261018212923",
    ]
}

#[test]
fn ssh_hit_replays_output_for_same_contents_at_new_path() {
    let fixture = Fixture::new();
    let allowed = fixture.write("allowed", b"user@example.com key");
    let first = fixture.write("first.sig", b"signature");
    let second = fixture.write("second.sig", b"signature");
    let miss = fixture.ssh(&verify_args(&first, &allowed), b"payload");
    let hit = fixture.ssh(&verify_args(&second, &allowed), b"payload");
    assert!(miss.status.success());
    assert!(hit.status.success());
    assert_eq!(hit.stdout, b"out from ssh-keygen\n");
    assert_eq!(hit.stderr, b"err from ssh-keygen\n");
    assert_eq!((miss.stdout, miss.stderr), (hit.stdout, hit.stderr));
    assert_eq!(fixture.calls().len(), 1);
}

#[test]
fn ssh_inputs_are_part_of_the_key() {
    let fixture = Fixture::new();
    let allowed = fixture.write("allowed", b"user@example.com key");
    let signature = fixture.write("sig", b"signature");
    let args = verify_args(&signature, &allowed);
    fixture.ssh(&args, b"payload");
    fixture.ssh(&args, b"other payload");
    fixture.write("sig", b"other signature");
    fixture.ssh(&args, b"payload");
    fixture.write("allowed", b"user@example.com other key");
    fixture.ssh(&args, b"payload");
    let mut other_time = args.clone();
    other_time[10] = "-Overify-time=20261019000000";
    fixture.ssh(&other_time, b"payload");
    assert_eq!(fixture.calls().len(), 5);
}

#[test]
fn ssh_find_principals_ignores_stdin() {
    let fixture = Fixture::new();
    let allowed = fixture.write("allowed", b"user@example.com key");
    let signature = fixture.write("sig", b"signature");
    let args = ["-Y", "find-principals", "-f", &allowed, "-s", &signature];
    fixture.ssh(&args, b"one");
    let hit = fixture.ssh(&args, b"two");
    assert_eq!(hit.stdout, b"out from ssh-keygen\n");
    assert_eq!(fixture.calls().len(), 1);
}

#[test]
fn ssh_failure_is_not_cached() {
    let fixture = Fixture::new();
    let allowed = fixture.write("allowed", b"user@example.com key");
    let signature = fixture.write("sig", b"signature");
    let args = verify_args(&signature, &allowed);
    for _ in 0..2 {
        let output = fixture.run(
            "wlr-git-ssh-verify",
            &args,
            b"payload",
            &[("FAKE_STATUS", "1")],
        );
        assert_eq!(output.status.code(), Some(1));
        assert_eq!(output.stdout, b"out from ssh-keygen\n");
    }
    assert_eq!(fixture.calls().len(), 2);
}

#[test]
fn ssh_signing_goes_to_the_signer() {
    for args in [
        vec!["-Y", "sign", "-n", "git", "-f", "key", "-"],
        vec!["-Ysign", "-n", "git", "-f", "key", "-"],
        vec!["-n", "git", "-Y", "sign", "-f", "key", "-"],
        vec!["-Y", "check-novalidate", "-n", "git", "-Y", "sign", "-"],
        vec![],
    ] {
        let fixture = Fixture::new();
        let output = fixture.ssh(&args, b"payload");
        assert_eq!(output.stdout, b"out from wlr-git-ssh-sign\n", "{args:?}");
        assert_eq!(
            fixture.calls(),
            [format!("wlr-git-ssh-sign {}", args.join(" "))]
        );
    }
}

#[test]
fn ssh_unknown_argument_form_goes_to_the_signer() {
    let fixture = Fixture::new();
    let signature = fixture.write("sig", b"signature");
    let joined = format!("-s{signature}");
    let args = ["-Y", "verify", "-n", "git", &joined];
    fixture.ssh(&args, b"payload");
    fixture.ssh(&args, b"payload");
    let call = format!("wlr-git-ssh-sign {}", args.join(" "));
    assert_eq!(fixture.calls(), [call.clone(), call]);
    assert!(!fixture.path("cache").exists());
}

#[test]
fn gpg_hit_and_keyring_invalidation() {
    let fixture = Fixture::new();
    let signature = fixture.write("sig", b"signature");
    fixture.gpg(&signature, b"payload", &[]);
    let hit = fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(hit.stdout, b"out from gpg\n");
    assert_eq!(hit.stderr, b"err from gpg\n");
    assert_eq!(fixture.calls().len(), 1);
    fixture.gpg(&signature, b"other payload", &[]);
    assert_eq!(fixture.calls().len(), 2);
    fixture.write("gnupg/trustdb.gpg", b"trust");
    fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(fixture.calls().len(), 3);
    fs::create_dir(fixture.path("gnupg/public-keys.d")).unwrap();
    fixture.write("gnupg/public-keys.d/pubring.db", b"keys");
    fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(fixture.calls().len(), 4);
    fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(fixture.calls().len(), 4);
}

#[test]
fn gpg_results_expire() {
    let fixture = Fixture::new();
    let signature = fixture.write("sig", b"signature");
    let expired = [("WLR_GIT_VERIFY_GPG_TTL", "0")];
    fixture.gpg(&signature, b"payload", &expired);
    fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(fixture.calls().len(), 2);
    fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(fixture.calls().len(), 2);
}

#[test]
fn gpg_results_end_when_the_signature_expires() {
    let fixture = Fixture::new();
    let signature = fixture.write("sig", b"signature");
    let expired = [(
        "FAKE_STDOUT",
        "[GNUPG:] VALIDSIG F 2024-02-11 1707675526 1707675527 4",
    )];
    fixture.gpg(&signature, b"payload", &expired);
    fixture.gpg(&signature, b"payload", &expired);
    assert_eq!(fixture.calls().len(), 2);
    let valid = [(
        "FAKE_STDOUT",
        "[GNUPG:] VALIDSIG F 2024-02-11 1707675526 0 4",
    )];
    fixture.gpg(&signature, b"payload", &valid);
    let hit = fixture.gpg(&signature, b"payload", &valid);
    assert_eq!(fixture.calls().len(), 3);
    assert_eq!(
        hit.stdout,
        b"[GNUPG:] VALIDSIG F 2024-02-11 1707675526 0 4\n"
    );
}

#[test]
fn gpg_outside_keyring_disables_the_cache() {
    let fixture = Fixture::new();
    let signature = fixture.write("sig", b"signature");
    fixture.write("gnupg/gpg.conf", b"keyring /elsewhere/pubring.kbx\n");
    fixture.gpg(&signature, b"payload", &[]);
    fixture.gpg(&signature, b"payload", &[]);
    assert_eq!(fixture.calls().len(), 2);
    assert!(!fixture.path("cache").exists());
}

#[test]
fn gpg_other_calls_go_to_gpg() {
    let fixture = Fixture::new();
    for args in [
        vec!["--status-fd=2", "-bsau", "KEY"],
        vec!["-Y", "verify", "-n", "git"],
        vec!["--keyid-format=long", "--status-fd=1", "--verify", "sig"],
    ] {
        fixture.run("wlr-git-gpg-verify", &args, b"payload", &[]);
        fixture.run("wlr-git-gpg-verify", &args, b"payload", &[]);
    }
    assert_eq!(fixture.calls().len(), 6);
    assert!(fixture.calls().iter().all(|call| call.starts_with("gpg ")));
    assert!(!Path::new(&fixture.path("cache")).exists());
}
