export const language = /^en(?:-|$)/i.test(navigator.language) ? 'en' : 'es';
export const locale = language === 'en' ? 'en-GB' : 'es-ES';

export function tr(es: string, en: string): string {
  return language === 'en' ? en : es;
}
