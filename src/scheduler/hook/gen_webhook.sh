# Set the service name and namespace for the certificate's Common Name (CN)
# This MUST match the service we create later.
SERVICE_NAME=custom-scheduler
NAMESPACE=openfaas-fn

# Generate the CA key and certificate
openssl req -x509 -newkey rsa:4096 -keyout ca.key -out ca.crt -days 365 -nodes \
  -subj "/C=US/ST=CA/L=Palo Alto/O=Artifact/CN=custom-scheduler-ca"

# Generate the server key
openssl genrsa -out tls.key 2048

# Create a certificate signing request (CSR) config
cat <<EOF > "csr.conf"
[ req ]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn

[ dn ]
C = US
ST = CA
L = Palo Alto
O = Artifact
CN = ${SERVICE_NAME}.${NAMESPACE}.svc

[ v3_ext ]
authorityKeyIdentifier=keyid,issuer:always
basicConstraints=CA:FALSE
keyUsage=keyEncipherment,dataEncipherment
extendedKeyUsage=serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${SERVICE_NAME}
DNS.2 = ${SERVICE_NAME}.${NAMESPACE}
DNS.3 = ${SERVICE_NAME}.${NAMESPACE}.svc
EOF

# Generate the CSR and sign it with the CA
openssl req -new -key tls.key -out tls.csr -config csr.conf
openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out tls.crt -days 365 -extensions v3_ext -extfile csr.conf