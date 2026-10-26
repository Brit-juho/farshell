// Idempotency keys are core-only: importing a history chunk here creates a boot cycle.
export const operationId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
