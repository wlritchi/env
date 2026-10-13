# browserpass with passage (age encryption) support
# Based on https://github.com/browserpass/browserpass-native/commit/dd761eafcabe4a0c1d26d4dabb9bf99c8e3ba48d
# Adapted for browserpass-native v3 code structure

{ pkgs, age-with-plugins }:

let
  ageBin = "${age-with-plugins}/bin/age";
in
pkgs.browserpass.overrideAttrs (oldAttrs: {
  postPatch = (oldAttrs.postPatch or "") + ''
        # Add AgeDecryptFile function to helpers/helpers.go
        substituteInPlace helpers/helpers.go \
          --replace-fail \
            'func GpgEncryptFile' \
            'func AgeIdentitiesFile() (string, error) {
    	if path := os.Getenv("PASSAGE_IDENTITIES_FILE"); path != "" {
    		return path, nil
    	}

    	home, err := os.UserHomeDir()
    	if err != nil {
    		return "", err
    	}

    	return filepath.Join(home, ".passage", "identities"), nil
    }

    func AgeDecryptFile(filePath string) (string, error) {
    	identityPath, err := AgeIdentitiesFile()
    	if err != nil {
    		return "", err
    	}

    	passwordFile, err := os.Open(filePath)
    	if err != nil {
    		return "", err
    	}
    	defer passwordFile.Close()

    	var stdout, stderr bytes.Buffer
    	ageOptions := []string{"--decrypt", "--identity", identityPath}

    	cmd := exec.Command("${ageBin}", ageOptions...)
    	cmd.Stdin = passwordFile
    	cmd.Stdout = &stdout
    	cmd.Stderr = &stderr

    	if err := cmd.Run(); err != nil {
    		return "", fmt.Errorf("Error: %s, Stderr: %s", err.Error(), stderr.String())
    	}

    	return stdout.String(), nil
    }

    func GpgEncryptFile'

        # Change extension check from .gpg to .age
        substituteInPlace request/fetch.go \
          --replace-fail \
            'HasSuffix(request.File, ".gpg")' \
            'HasSuffix(request.File, ".age")'

        # Update error message in fetch.go
        substituteInPlace request/fetch.go \
          --replace-fail \
            "does not have the expected '.gpg' extension" \
            "does not have the expected '.age' extension"

        # Replace GpgDecryptFile call with AgeDecryptFile
        substituteInPlace request/fetch.go \
          --replace-fail \
            'responseData.Contents, err = helpers.GpgDecryptFile(filepath.Join(store.Path, request.File), gpgPath)' \
            'responseData.Contents, err = helpers.AgeDecryptFile(filepath.Join(store.Path, request.File))'

        # Update glob pattern in list.go to look for .age files
        substituteInPlace request/list.go \
          --replace-fail \
            'filepath.Join(store.Path, "/**/*.gpg")' \
            'filepath.Join(store.Path, "/**/*.age")'

        # Use passage's store location and variable for the default store
        substituteInPlace request/configure.go \
          --replace-fail \
            'os.Getenv("PASSWORD_STORE_DIR")' \
            'os.Getenv("PASSAGE_DIR")' \
          --replace-fail \
            'filepath.Join(home, ".password-store")' \
            'filepath.Join(home, ".passage", "store")'
  '';
})
