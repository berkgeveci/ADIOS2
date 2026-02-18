/*
 * Distributed under the OSI-approved Apache License, Version 2.0.  See
 * accompanying file Copyright.txt for details.
 *
 * SealedEncryptionOperator.cpp
 *
 *  Created on: Feb 18, 2026
 *      Author: Berk Geveci
 */

#include "SealedEncryptionOperator.h"

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>

#include <sodium.h>

namespace adios2
{
namespace plugin
{

/// Decode a hex string (2*len chars) into a byte buffer of length len.
/// Throws on invalid input.
static void HexDecode(const std::string &hex, unsigned char *out, size_t len)
{
    if (hex.size() != 2 * len)
    {
        throw std::runtime_error("SealedEncryptionOperator: hex string has wrong length. "
                                 "Expected " +
                                 std::to_string(2 * len) + " chars, got " +
                                 std::to_string(hex.size()));
    }
    if (sodium_hex2bin(out, len, hex.c_str(), hex.size(), nullptr, nullptr, nullptr) != 0)
    {
        throw std::runtime_error("SealedEncryptionOperator: invalid hex string");
    }
}

struct SealedEncryptionOperator::Impl
{
    unsigned char PublicKey[crypto_box_PUBLICKEYBYTES] = {0};
    unsigned char SecretKey[crypto_box_SECRETKEYBYTES] = {0};
    bool PublicKeyValid = false;
    bool SecretKeyValid = false;

    ~Impl()
    {
        sodium_munlock(PublicKey, crypto_box_PUBLICKEYBYTES);
        sodium_munlock(SecretKey, crypto_box_SECRETKEYBYTES);
    }

    void ReadPublicKey(const std::string &filename)
    {
        std::ifstream keyFile(filename, std::ios::binary);
        if (!keyFile)
        {
            throw std::runtime_error("SealedEncryptionOperator: could not open public key file: " +
                                     filename);
        }
        keyFile.read(reinterpret_cast<char *>(PublicKey), crypto_box_PUBLICKEYBYTES);
        if (!keyFile)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: public key file is too short: " + filename);
        }
        keyFile.close();

        if (sodium_mlock(PublicKey, crypto_box_PUBLICKEYBYTES) == -1)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: unable to lock memory for public key");
        }
        PublicKeyValid = true;
    }

    void SetPublicKeyHex(const std::string &hex)
    {
        HexDecode(hex, PublicKey, crypto_box_PUBLICKEYBYTES);
        if (sodium_mlock(PublicKey, crypto_box_PUBLICKEYBYTES) == -1)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: unable to lock memory for public key");
        }
        PublicKeyValid = true;
    }

    void ReadSecretKey(const std::string &filename)
    {
        std::ifstream keyFile(filename, std::ios::binary);
        if (!keyFile)
        {
            throw std::runtime_error("SealedEncryptionOperator: could not open secret key file: " +
                                     filename);
        }
        keyFile.read(reinterpret_cast<char *>(SecretKey), crypto_box_SECRETKEYBYTES);
        if (!keyFile)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: secret key file is too short: " + filename);
        }
        keyFile.close();

        if (sodium_mlock(SecretKey, crypto_box_SECRETKEYBYTES) == -1)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: unable to lock memory for secret key");
        }
        SecretKeyValid = true;
    }

    void SetSecretKeyHex(const std::string &hex)
    {
        HexDecode(hex, SecretKey, crypto_box_SECRETKEYBYTES);
        if (sodium_mlock(SecretKey, crypto_box_SECRETKEYBYTES) == -1)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: unable to lock memory for secret key");
        }
        SecretKeyValid = true;
    }
};

SealedEncryptionOperator::SealedEncryptionOperator(const Params &parameters)
: PluginOperatorInterface(parameters), m_Impl(new Impl)
{
    if (sodium_init() < 0)
    {
        throw std::runtime_error("libsodium could not be initialized");
    }

    // Public key: file param, then hex param
    auto pkFileIt = m_Parameters.find("publickeyfile");
    auto pkHexIt = m_Parameters.find("publickey");
    if (pkFileIt != m_Parameters.end())
    {
        m_Impl->ReadPublicKey(pkFileIt->second);
    }
    else if (pkHexIt != m_Parameters.end())
    {
        m_Impl->SetPublicKeyHex(pkHexIt->second);
    }

    // Secret key lookup order:
    // 1. SecretKeyFile param
    // 2. SecretKey param (hex)
    // 3. ADIOS2_SECRET_KEY_FILE env var
    // 4. ADIOS2_SECRET_KEY env var (hex)
    auto skFileIt = m_Parameters.find("secretkeyfile");
    auto skHexIt = m_Parameters.find("secretkey");
    if (skFileIt != m_Parameters.end())
    {
        m_Impl->ReadSecretKey(skFileIt->second);
    }
    else if (skHexIt != m_Parameters.end())
    {
        m_Impl->SetSecretKeyHex(skHexIt->second);
    }
    else
    {
        const char *envSKFile = std::getenv("ADIOS2_SECRET_KEY_FILE");
        if (envSKFile && envSKFile[0] != '\0')
        {
            m_Impl->ReadSecretKey(std::string(envSKFile));
        }
        else
        {
            const char *envSKHex = std::getenv("ADIOS2_SECRET_KEY");
            if (envSKHex && envSKHex[0] != '\0')
            {
                m_Impl->SetSecretKeyHex(std::string(envSKHex));
            }
        }
    }

    // If we have a secret key but no public key, derive the public key
    // from the secret key (Curve25519: pk = sk * basepoint)
    if (m_Impl->SecretKeyValid && !m_Impl->PublicKeyValid)
    {
        if (crypto_scalarmult_base(m_Impl->PublicKey, m_Impl->SecretKey) != 0)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: failed to derive public key from secret key");
        }
        if (sodium_mlock(m_Impl->PublicKey, crypto_box_PUBLICKEYBYTES) == -1)
        {
            throw std::runtime_error(
                "SealedEncryptionOperator: unable to lock memory for public key");
        }
        m_Impl->PublicKeyValid = true;
    }
}

SealedEncryptionOperator::~SealedEncryptionOperator() {}

#if defined(__clang__)
#if __has_feature(memory_sanitizer)
// Memory Sanitizer has an issue with some libsodium calls.
__attribute__((no_sanitize("memory")))
#endif
#endif
size_t
SealedEncryptionOperator::Operate(const char *dataIn, const Dims &blockStart,
                                  const Dims &blockCount, const DataType type, char *bufferOut)
{
    if (!m_Impl->PublicKeyValid)
    {
        throw std::runtime_error("SealedEncryptionOperator::Operate was called, but"
                                 " a valid public key has not been loaded. "
                                 "Did you add the PublicKeyFile"
                                 " param when setting up the operator?");
    }

    size_t offset = 0;

    // 1. Write original data size
    size_t sizeIn = helper::GetTotalSize(blockCount, helper::GetDataTypeSize(type));
    PutParameter(bufferOut, offset, sizeIn);

    // 2. Generate random session key, seal it with public key
    unsigned char sessionKey[crypto_secretbox_KEYBYTES];
    crypto_secretbox_keygen(sessionKey);

    const size_t sealedKeySize = crypto_box_SEALBYTES + crypto_secretbox_KEYBYTES;
    unsigned char *sealedKey = reinterpret_cast<unsigned char *>(bufferOut + offset);
    crypto_box_seal(sealedKey, sessionKey, crypto_secretbox_KEYBYTES, m_Impl->PublicKey);
    offset += sealedKeySize;

    // 3. Generate random nonce
    unsigned char *nonce = reinterpret_cast<unsigned char *>(bufferOut + offset);
    randombytes_buf(nonce, crypto_secretbox_NONCEBYTES);
    offset += crypto_secretbox_NONCEBYTES;

    // 4. Encrypt data with session key
    size_t cipherTextSize = sizeIn + crypto_secretbox_MACBYTES;
    unsigned char *cipherText = reinterpret_cast<unsigned char *>(bufferOut + offset);
    crypto_secretbox_easy(cipherText, reinterpret_cast<const unsigned char *>(dataIn), sizeIn,
                          nonce, sessionKey);
    offset += cipherTextSize;

    // 5. Wipe session key from memory
    sodium_memzero(sessionKey, crypto_secretbox_KEYBYTES);

    return offset;
}

#if defined(__clang__)
#if __has_feature(memory_sanitizer)
// Memory Sanitizer has an issue with some libsodium calls.
__attribute__((no_sanitize("memory")))
#endif
#endif
size_t
SealedEncryptionOperator::InverseOperate(const char *bufferIn, const size_t sizeIn, char *dataOut)
{
    if (!m_Impl->PublicKeyValid || !m_Impl->SecretKeyValid)
    {
        throw std::runtime_error(
            "SealedEncryptionOperator::InverseOperate was called, but"
            " valid public and secret keys have not been loaded. "
            "Set PublicKeyFile/SecretKeyFile params, or set"
            " ADIOS2_SECRET_KEY_FILE or ADIOS2_SECRET_KEY env var.");
    }

    size_t offset = 0;

    // 1. Read original data size
    const size_t dataBytes = GetParameter<size_t>(bufferIn, offset);

    // 2. Unseal the session key
    const size_t sealedKeySize = crypto_box_SEALBYTES + crypto_secretbox_KEYBYTES;
    const unsigned char *sealedKey = reinterpret_cast<const unsigned char *>(bufferIn + offset);
    offset += sealedKeySize;

    unsigned char sessionKey[crypto_secretbox_KEYBYTES];
    if (crypto_box_seal_open(sessionKey, sealedKey, sealedKeySize, m_Impl->PublicKey,
                             m_Impl->SecretKey) != 0)
    {
        throw std::runtime_error(
            "SealedEncryptionOperator: failed to unseal session key. "
            "Wrong key pair?");
    }

    // 3. Read nonce
    const unsigned char *nonce = reinterpret_cast<const unsigned char *>(bufferIn + offset);
    offset += crypto_secretbox_NONCEBYTES;

    // 4. Decrypt data
    size_t cipherTextSize = dataBytes + crypto_secretbox_MACBYTES;
    const unsigned char *cipherText = reinterpret_cast<const unsigned char *>(bufferIn + offset);
    offset += cipherTextSize;

    if (crypto_secretbox_open_easy(reinterpret_cast<unsigned char *>(dataOut), cipherText,
                                   cipherTextSize, nonce, sessionKey) != 0)
    {
        sodium_memzero(sessionKey, crypto_secretbox_KEYBYTES);
        throw std::runtime_error("SealedEncryptionOperator: decryption failed, message forged!");
    }

    // 5. Wipe session key
    sodium_memzero(sessionKey, crypto_secretbox_KEYBYTES);

    return dataBytes;
}

bool SealedEncryptionOperator::IsDataTypeValid(const DataType type) const { return true; }

size_t SealedEncryptionOperator::GetEstimatedSize(const size_t ElemCount, const size_t ElemSize,
                                                  const size_t ndims, const size_t *dims) const
{
    size_t sizeIn = ElemCount * ElemSize;
    return (sizeof(size_t)                                           // Data size
            + (crypto_box_SEALBYTES + crypto_secretbox_KEYBYTES)     // Sealed session key
            + crypto_secretbox_NONCEBYTES                            // Nonce
            + sizeIn                                                 // Data
            + crypto_secretbox_MACBYTES                              // MAC
    );
}

} // end namespace plugin
} // end namespace adios2

extern "C" {

adios2::plugin::SealedEncryptionOperator *OperatorCreate(const adios2::Params &parameters)
{
    return new adios2::plugin::SealedEncryptionOperator(parameters);
}

void OperatorDestroy(adios2::plugin::SealedEncryptionOperator *obj) { delete obj; }
}
