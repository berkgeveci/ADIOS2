/*
 * Distributed under the OSI-approved Apache License, Version 2.0.  See
 * accompanying file Copyright.txt for details.
 *
 * SealedEncryptionOperator.h
 *
 *  Created on: Feb 18, 2026
 *      Author: Berk Geveci
 */

#ifndef SEALEDENCRYPTIONOPERATOR_H_
#define SEALEDENCRYPTIONOPERATOR_H_

#include <memory>

#include "adios2/common/ADIOSTypes.h"
#include "adios2/operator/plugin/PluginOperatorInterface.h"

namespace adios2
{
namespace plugin
{

/** SealedEncryptionOperator uses libsodium's crypto_box_seal for asymmetric
 * (public-key) encryption. Only the public key is needed for writing; both
 * public and secret keys are needed for reading.
 *
 * A hybrid scheme is used per block: a random session key is sealed with
 * the public key (crypto_box_seal), then bulk data is encrypted with the
 * session key (crypto_secretbox).
 *
 * Parameters:
 *   PublicKeyFile  - path to 32-byte Curve25519 public key
 *   PublicKey      - hex-encoded public key (64 hex chars)
 *   SecretKeyFile  - path to 32-byte secret key
 *   SecretKey      - hex-encoded secret key (64 hex chars)
 *
 * Secret key fallback order: SecretKeyFile param, SecretKey param,
 *   ADIOS2_SECRET_KEY_FILE env var, ADIOS2_SECRET_KEY env var.
 *
 * Use adios2_seal_keygen to generate the key pair.
 */
class SealedEncryptionOperator : public PluginOperatorInterface
{
public:
    SealedEncryptionOperator(const Params &parameters);
    virtual ~SealedEncryptionOperator();

    size_t Operate(const char *dataIn, const Dims &blockStart, const Dims &blockCount,
                   const DataType type, char *bufferOut) override;

    size_t InverseOperate(const char *bufferIn, const size_t sizeIn, char *dataOut) override;

    bool IsDataTypeValid(const DataType type) const override;

    size_t GetEstimatedSize(const size_t ElemCount, const size_t ElemSize, const size_t ndims,
                            const size_t *dims) const override;

private:
    struct Impl;
    std::unique_ptr<Impl> m_Impl;
};

} // end namespace plugin
} // end namespace adios2

extern "C" {

adios2::plugin::SealedEncryptionOperator *OperatorCreate(const adios2::Params &parameters);
void OperatorDestroy(adios2::plugin::SealedEncryptionOperator *obj);
}

#endif /* SEALEDENCRYPTIONOPERATOR_H_ */
