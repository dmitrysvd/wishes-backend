const { Connect, Config } = window.SuperAppKit;

const appId = window.appId;
const authRedirectUrl = window.authRedirectUrl;

Config.init({
  appId: appId, // идентификатор приложения
});

// url должен иметь https схему! При использовании http будет бесконечная загрузка
// В url в query-string передастся параметр payload с данными авторизации, формат данных в payload - VKSilentTokenPayload.
// Если передать параметр state, то после успешной авторизации в url
// в query-string будет передан параметр state имеющий такое же значение
// как и переданное в Connect.redirectAuth метод.
Connect.redirectAuth({ url: authRedirectUrl, state: '1234'});
