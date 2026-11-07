export const CONNECTED_RADIO_SCOPE = 'recommend:read';

export function hasConnectedRadio(device) {
  return Array.isArray(device?.scopes) && device.scopes.includes(CONNECTED_RADIO_SCOPE);
}
