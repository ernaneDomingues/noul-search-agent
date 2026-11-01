// Install page: highlight the download for the visitor's OS and copy the connector URL.
(function () {
  'use strict';
  var platform = ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '').toLowerCase();
  var os = platform.indexOf('win') === 0 ? 'windows' : platform.indexOf('mac') === 0 ? 'macos' : platform.indexOf('linux') !== -1 ? 'linux' : '';
  if (os) {
    document.documentElement.setAttribute('data-os', os);
    document.querySelectorAll('[data-os-target="' + os + '"]').forEach(function (el) {
      el.classList.add('is-recommended');
    });
    var label = document.getElementById('detected-os');
    if (label) label.textContent = { windows: 'Windows', macos: 'macOS', linux: 'Linux' }[os];
  }
})();
