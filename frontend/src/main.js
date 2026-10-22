const bankRoute = window.location.pathname === '/bank' || window.location.pathname.startsWith('/bank/');

if (bankRoute) {
  import('./bank_Demo/app.js');
} else {
  import('./app.js');
}
