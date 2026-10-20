#/bin/bash
mkdir -p ~/.ansible
read -rsp "choose a vault password: " V; echo
printf '%s\n' "$V" > ~/.ansible/vault_pass; chmod 600 ~/.ansible/vault_pass; unset V

for h in "$@"; do
  mkdir -p host_vars/$h
  read -rsp "sudo password for svc-$h: " P; echo
  printf '%s' "$P" | ansible-vault encrypt_string \
      --vault-password-file ~/.ansible/vault_pass \
      --stdin-name ansible_become_password > host_vars/$h/vault.yml
  unset P
done
chmod 600 host_vars/*/vault.yml
