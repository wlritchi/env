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
            'func AgeDecryptFile(filePath string, identityPath string) (string, error) {
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
            'passwordFilePath := filepath.Join(store.Path, "store", request.File)
    	identityFilePath := filepath.Join(store.Path, "identities")
    	responseData.Contents, err = helpers.AgeDecryptFile(passwordFilePath, identityFilePath)'

        # Update glob pattern in list.go to look for .age files in store/ subdirectory
        substituteInPlace request/list.go \
          --replace-fail \
            'filepath.Join(store.Path, "/**/*.gpg")' \
            'filepath.Join(store.Path, "store", "/**/*.age")'

        # Update relative path calculation in list.go
        substituteInPlace request/list.go \
          --replace-fail \
            'relativePath, err := filepath.Rel(store.Path, file)' \
            'relativePath, err := filepath.Rel(filepath.Join(store.Path, "store"), file)'
  '';
})
