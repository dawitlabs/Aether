{
  description = "Aether development environment";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: {
    devShells.x86_64-linux.default =
      let
        pkgs = nixpkgs.legacyPackages.x86_64-linux;
      in
      pkgs.mkShell {
        packages = [ pkgs.python312 pkgs.neo4j ];
        AETHER_NEO4J_DIST = "${pkgs.neo4j}/share/neo4j";
      };
  };
}
