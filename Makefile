.PHONY: run clean flatpak-build flatpak-run flatpak-clean

run:
	python3 -m ekran.main

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +

flatpak-build:
	flatpak-builder --force-clean --user --install build io.github.ekran.Ekran.yml

flatpak-run:
	flatpak run io.github.ekran.Ekran

flatpak-clean:
	rm -rf build .flatpak-builder
