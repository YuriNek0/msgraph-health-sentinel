{
  description = "MSGraph Health Sentinel";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      mkPackage = pkgs:
        pkgs.writeShellApplication {
          name = "msgraph-health-sentinel";
          runtimeInputs = [
            (pkgs.python3.withPackages (ps: [ ps.requests ]))
          ];
          text = ''
            exec python "${self}/fetch.py" "$@"
          '';
        };
    in
    (flake-utils.lib.eachSystem systems (system:
      let
        pkgs = import nixpkgs { inherit system; };
        package = mkPackage pkgs;
      in
      {
        packages = {
          default = package;
          msgraph-health-sentinel = package;
        };

        apps.default = {
          type = "app";
          program = "${package}/bin/msgraph-health-sentinel";
        };
      }))
    // {
      homeManagerModules = {
        default = import ./nix/home-manager/msgraph-health-sentinel.nix;
        msgraph-health-sentinel = self.homeManagerModules.default;
      };
    };
}
